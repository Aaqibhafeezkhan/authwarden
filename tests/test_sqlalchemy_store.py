import os
from datetime import timedelta

import httpx
import pytest
import pytest_asyncio
import respx
import sqlalchemy as sa
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from authwarden import AuthWarden, OAuthAccount, OAuthProviderConfig, UserInDB, WardenConfig
from authwarden.storage.base import AbstractUserStore
from authwarden.storage.sqlalchemy import Base, SQLAlchemyUserStore, create_tables
from authwarden.utils import utcnow

EXTERNAL_URL = os.environ.get("AUTHWARDEN_TEST_DATABASE_URL")
DATABASES = ["sqlite", "external"] if EXTERNAL_URL else ["sqlite"]


@pytest_asyncio.fixture(params=DATABASES)
async def store(request, tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}" if request.param == "sqlite" else EXTERNAL_URL
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await create_tables(engine)
    yield SQLAlchemyUserStore(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


def make_user(email: str = "user@example.com", **fields) -> UserInDB:
    return UserInDB(email=email, hashed_password="hash", **fields)


def make_account(user_id: str, provider: str = "google", provider_user_id: str = "g-1") -> OAuthAccount:
    return OAuthAccount(
        user_id=user_id,
        provider=provider,
        provider_user_id=provider_user_id,
        email="user@example.com",
        access_token="access",
        refresh_token="refresh",
        token_expires_at=utcnow() + timedelta(hours=1),
    )


def test_store_satisfies_the_protocol(store):
    assert isinstance(store, AbstractUserStore)


async def test_every_field_survives_a_round_trip(store):
    now = utcnow()
    user = UserInDB(
        email="Full@Example.com",
        username="fulluser",
        full_name="Full User",
        hashed_password="hash",
        phone_number="+2348011112222",
        phone_verified=True,
        is_verified=True,
        is_superuser=True,
        roles=["admin"],
        scopes=["posts:read", "posts:write"],
        extra_data={"plan": {"tier": "pro"}},
        mfa_enabled=True,
        mfa_secret="SECRET",
        mfa_pending_secret="PENDING",
        backup_codes=["h1", "h2"],
        last_verification_sent_at=now,
        verification_otp_hash="otp",
        verification_otp_expires_at=now + timedelta(minutes=10),
        verification_otp_attempts=2,
        last_reset_request_at=now,
        reset_token_hash="reset",
        reset_token_used_at=now,
        reset_otp_hash="reset-otp",
        reset_otp_expires_at=now + timedelta(minutes=10),
        reset_otp_attempts=1,
        failed_login_attempts=3,
        locked_until=now + timedelta(minutes=15),
    )
    await store.create(user)

    assert (await store.get_by_id(user.id)).model_dump() == user.model_dump()


async def test_lookups_by_every_identifier(store):
    user = make_user("Mixed@Example.com", username="MixedName", phone_number="+1555")
    await store.create(user)

    assert (await store.get_by_email("mixed@example.com")).id == user.id
    assert (await store.get_by_username("MIXEDNAME")).id == user.id
    assert (await store.get_by_phone("+1555")).id == user.id


async def test_missing_users_return_none(store):
    assert await store.get_by_id("nope") is None
    assert await store.get_by_email("nope@example.com") is None
    assert await store.get_by_username("nope") is None
    assert await store.get_by_phone("+000") is None


async def test_update_persists_changes_and_moves_identifiers(store):
    user = make_user("old@example.com", username="oldname")
    await store.create(user)

    user.email = "new@example.com"
    user.username = None
    user.is_active = False
    user.failed_login_attempts = 4
    await store.update(user)

    assert await store.get_by_email("old@example.com") is None
    assert await store.get_by_username("oldname") is None
    loaded = await store.get_by_email("new@example.com")
    assert loaded.is_active is False
    assert loaded.failed_login_attempts == 4


async def test_datetimes_come_back_timezone_aware(store):
    user = make_user(locked_until=utcnow() + timedelta(minutes=5))
    await store.create(user)

    loaded = await store.get_by_id(user.id)

    assert loaded.locked_until.tzinfo is not None
    assert loaded.locked_until > utcnow()


@pytest.mark.parametrize(
    "duplicate",
    [
        {"email": "DUP@example.com"},
        {"username": "TAKEN"},
        {"phone_number": "+1999"},
    ],
)
async def test_duplicate_identifiers_are_rejected(store, duplicate):
    await store.create(make_user("dup@example.com", username="taken", phone_number="+1999"))
    fields = {"email": "other@example.com", "username": "other", "phone_number": "+1888", **duplicate}

    with pytest.raises(sa.exc.IntegrityError):
        await store.create(make_user(**fields))


async def test_users_without_username_or_phone_can_coexist(store):
    await store.create(make_user("a@example.com"))
    await store.create(make_user("b@example.com"))

    assert (await store.get_by_email("a@example.com")).username is None
    assert (await store.get_by_email("b@example.com")).phone_number is None


async def test_oauth_account_lifecycle(store):
    user = make_user()
    await store.create(user)
    account = make_account(user.id)

    await store.create_oauth_account(account)
    assert (await store.get_oauth_account("google", "g-1")).id == account.id
    assert [a.id for a in await store.get_oauth_accounts_for_user(user.id)] == [account.id]

    account.email = "changed@example.com"
    await store.update_oauth_account(account)
    assert (await store.get_oauth_account("google", "g-1")).email == "changed@example.com"

    await store.delete_oauth_account(user.id, "google")
    assert await store.get_oauth_account("google", "g-1") is None


async def test_oauth_provider_identity_is_unique(store):
    user = make_user()
    await store.create(user)
    await store.create_oauth_account(make_account(user.id))

    with pytest.raises(sa.exc.IntegrityError):
        await store.create_oauth_account(make_account(user.id))


async def test_delete_removes_the_user_and_their_oauth_accounts(store):
    user, other = make_user("a@example.com"), make_user("b@example.com")
    await store.create(user)
    await store.create(other)
    await store.create_oauth_account(make_account(user.id))
    await store.create_oauth_account(make_account(other.id, provider_user_id="g-2"))

    await store.delete(user.id)

    assert await store.get_by_id(user.id) is None
    assert await store.get_oauth_account("google", "g-1") is None
    assert await store.get_oauth_account("google", "g-2") is not None


@pytest_asyncio.fixture
async def app_client(store):
    config = WardenConfig(
        secret_key="sqlalchemy-store-secret-key-32-bytes",
        require_email_verification=False,
        max_failed_attempts=2,
        oauth_providers={
            "google": OAuthProviderConfig(client_id="id", client_secret="secret", redirect_uri="https://app.test/cb")
        },
    )
    warden = AuthWarden(config=config, user_store=store)
    app = FastAPI()
    app.include_router(warden.router, prefix="/auth")

    @app.get("/profile")
    async def profile(user=Depends(warden.current_user)):
        return {"email": user.email}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_register_login_and_protected_route(app_client):
    await app_client.post("/auth/register", json={"email": "e2e@example.com", "password": "strongpass123"})

    login = await app_client.post("/auth/login", json={"identifier": "e2e@example.com", "password": "strongpass123"})
    token = login.json()["access_token"]
    profile = await app_client.get("/profile", headers={"Authorization": f"Bearer {token}"})

    assert login.status_code == 200
    assert profile.json() == {"email": "e2e@example.com"}


async def test_login_lockout_persists_across_requests(app_client):
    await app_client.post("/auth/register", json={"email": "lock@example.com", "password": "strongpass123"})
    for _ in range(2):
        wrong = await app_client.post("/auth/login", json={"identifier": "lock@example.com", "password": "wrong"})
        assert wrong.status_code == 401

    locked = await app_client.post("/auth/login", json={"identifier": "lock@example.com", "password": "strongpass123"})

    assert locked.status_code == 403


@respx.mock
async def test_oauth_login_creates_a_user_and_a_link(app_client, store):
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "token_type": "bearer", "expires_in": 3600})
    )
    respx.get("https://openidconnect.googleapis.com/v1/userinfo").mock(
        return_value=httpx.Response(200, json={"sub": "g-7", "email": "oauth@example.com", "email_verified": True})
    )

    async def sign_in():
        authorize = await app_client.get("/auth/oauth/google/authorize")
        state = authorize.json()["authorization_url"].split("state=")[1].split("&")[0]
        return await app_client.post("/auth/oauth/google/callback", json={"code": "code", "state": state})

    first, second = await sign_in(), await sign_in()

    assert first.json()["is_new_user"] is True
    assert second.json()["is_new_user"] is False
    accounts = await store.get_oauth_accounts_for_user(first.json()["user"]["id"])
    assert len(accounts) == 1
    assert accounts[0].access_token != "tok"