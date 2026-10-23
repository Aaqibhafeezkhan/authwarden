import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from authwarden import AuthWarden, MemoryUserStore, OAuthProviderConfig, UserInDB, WardenConfig
from authwarden.authentication.oauth import FacebookOAuthProvider, GitHubOAuthProvider, MicrosoftOAuthProvider

PROVIDER = OAuthProviderConfig(client_id="id", client_secret="secret", redirect_uri="https://app.test/cb")


def make_warden(require_email_verification: bool = True) -> AuthWarden:
    config = WardenConfig(
        secret_key="oauth-linking-secret-key-32-bytes",
        require_email_verification=require_email_verification,
        oauth_providers={"google": PROVIDER},
    )
    return AuthWarden(config=config, user_store=MemoryUserStore())


def mock_google(email: str, verified: bool, sub: str = "g-1") -> None:
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "token_type": "bearer"})
    )
    respx.get("https://openidconnect.googleapis.com/v1/userinfo").mock(
        return_value=httpx.Response(200, json={"sub": sub, "email": email, "email_verified": verified})
    )


async def google_callback(warden: AuthWarden) -> httpx.Response:
    app = FastAPI()
    app.include_router(warden.router, prefix="/auth")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        authorize = await c.get("/auth/oauth/google/authorize")
        state = authorize.json()["authorization_url"].split("state=")[1].split("&")[0]
        return await c.post("/auth/oauth/google/callback", json={"code": "code", "state": state})


async def add_local_user(warden: AuthWarden, email: str, verified: bool = True) -> UserInDB:
    return await warden.store.create(
        UserInDB(email=email, hashed_password="x", is_active=verified, is_verified=verified)
    )


@respx.mock
@pytest.mark.asyncio
async def test_unverified_provider_email_does_not_link():
    warden = make_warden()
    user = await add_local_user(warden, "victim@example.com")
    mock_google("victim@example.com", verified=False)

    response = await google_callback(warden)

    assert response.status_code == 409
    assert await warden.store.get_oauth_accounts_for_user(user.id) == []


@respx.mock
@pytest.mark.asyncio
async def test_verified_provider_email_links():
    warden = make_warden()
    user = await add_local_user(warden, "owner@example.com")
    mock_google("owner@example.com", verified=True)

    response = await google_callback(warden)

    assert response.status_code == 200
    assert response.json()["is_new_user"] is False
    assert response.json()["user"]["id"] == user.id


@respx.mock
@pytest.mark.asyncio
async def test_unverified_provider_email_still_registers_new_user():
    warden = make_warden()
    mock_google("new@example.com", verified=False)

    response = await google_callback(warden)

    assert response.status_code == 200
    assert response.json()["is_new_user"] is True


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_cls", "url", "payload"),
    [
        (MicrosoftOAuthProvider, "https://graph.microsoft.com/oidc/userinfo", {"sub": "m-1", "email": "a@example.com"}),
        (FacebookOAuthProvider, "https://graph.facebook.com/me", {"id": "f-1", "email": "a@example.com"}),
    ],
)
async def test_providers_without_a_verified_flag_are_unverified(provider_cls, url, payload):
    respx.get(url).mock(return_value=httpx.Response(200, json=payload))

    userinfo = await provider_cls(PROVIDER).fetch_userinfo({"access_token": "tok"})

    assert userinfo.email == "a@example.com"
    assert userinfo.email_verified is False


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("emails", "expected"),
    [
        ([{"email": "p@example.com", "primary": True, "verified": False}], None),
        ([{"email": "p@example.com", "primary": True, "verified": True}], "p@example.com"),
    ],
)
async def test_github_fallback_requires_verified_primary_email(emails, expected):
    respx.get("https://api.github.com/user").mock(return_value=httpx.Response(200, json={"id": 1, "email": None}))
    respx.get("https://api.github.com/user/emails").mock(return_value=httpx.Response(200, json=emails))

    userinfo = await GitHubOAuthProvider(PROVIDER).fetch_userinfo({"access_token": "tok"})

    assert userinfo.email == expected

@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize("verified", [True, False])
async def test_new_oauth_user_is_verified_only_if_provider_verified(verified):
    warden = make_warden()
    mock_google("new@example.com", verified=verified)

    response = await google_callback(warden)

    assert response.json()["user"]["is_verified"] is verified


@respx.mock
@pytest.mark.asyncio
async def test_inactive_user_cannot_sign_in_through_existing_link():
    warden = make_warden()
    mock_google("user@example.com", verified=True)
    first = await google_callback(warden)
    user = await warden.store.get_by_id(first.json()["user"]["id"])
    user.is_active = False
    await warden.store.update(user)

    response = await google_callback(warden)

    assert response.status_code == 403


@respx.mock
@pytest.mark.asyncio
async def test_unverified_local_account_is_not_linked():
    warden = make_warden()
    user = await add_local_user(warden, "pending@example.com", verified=False)
    mock_google("pending@example.com", verified=True)

    response = await google_callback(warden)

    assert response.status_code == 409
    assert await warden.store.get_oauth_accounts_for_user(user.id) == []


@respx.mock
@pytest.mark.asyncio
async def test_account_created_from_unverified_email_is_not_linkable():
    warden = make_warden()
    mock_google("victim@example.com", verified=False, sub="g-attacker")
    first = await google_callback(warden)
    assert first.status_code == 200

    mock_google("victim@example.com", verified=True, sub="g-victim")
    response = await google_callback(warden)

    assert response.status_code == 409


@respx.mock
@pytest.mark.asyncio
async def test_no_email_linking_when_email_verification_is_disabled():
    warden = make_warden(require_email_verification=False)
    user = await add_local_user(warden, "local@example.com")
    mock_google("local@example.com", verified=True)

    response = await google_callback(warden)

    assert response.status_code == 409
    assert await warden.store.get_oauth_accounts_for_user(user.id) == []