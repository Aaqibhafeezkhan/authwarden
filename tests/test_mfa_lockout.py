import time

import pyotp
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from authwarden import AuthWarden, MemoryUserStore, WardenConfig

EMAIL = "mfa@example.com"
PASSWORD = "strongpass123"


@pytest_asyncio.fixture
async def mfa_client():
    config = WardenConfig(
        secret_key="mfa-lockout-secret-key-32-bytes!",
        require_email_verification=False,
        enable_mfa=True,
        max_failed_attempts=3,
    )
    warden = AuthWarden(config=config, user_store=MemoryUserStore())
    app = FastAPI()
    app.include_router(warden.router, prefix="/auth")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})
        user = await warden.store.get_by_email(EMAIL)
        secret = pyotp.random_base32()
        user.mfa_enabled = True
        user.mfa_secret = secret
        await warden.store.update(user)
        yield client, warden, secret


def wrong_code(secret: str) -> str:
    totp = pyotp.TOTP(secret)
    valid = {totp.at(time.time() + offset) for offset in (-30, 0, 30)}
    return next(code for code in ("000000", "111111") if code not in valid)


async def login(client, totp_code=None):
    return await client.post(
        "/auth/login", json={"identifier": EMAIL, "password": PASSWORD, "totp_code": totp_code}
    )


async def test_wrong_totp_codes_lock_the_account(mfa_client):
    client, _, secret = mfa_client
    for _ in range(3):
        assert (await login(client, wrong_code(secret))).status_code == 401

    response = await login(client, pyotp.TOTP(secret).now())

    assert response.status_code == 403
    assert "locked" in response.json()["detail"].lower()


async def test_missing_totp_code_does_not_count_as_a_failed_attempt(mfa_client):
    client, warden, secret = mfa_client
    for _ in range(5):
        assert (await login(client)).status_code == 403

    user = await warden.store.get_by_email(EMAIL)

    assert user.failed_login_attempts == 0
    assert (await login(client, pyotp.TOTP(secret).now())).status_code == 200


async def test_login_accepts_the_adjacent_totp_window(mfa_client):
    client, _, secret = mfa_client

    response = await login(client, pyotp.TOTP(secret).at(time.time() + 30))

    assert response.status_code == 200


async def test_successful_login_resets_the_failed_attempt_counter(mfa_client):
    client, warden, secret = mfa_client
    for _ in range(2):
        await login(client, wrong_code(secret))

    assert (await login(client, pyotp.TOTP(secret).now())).status_code == 200

    user = await warden.store.get_by_email(EMAIL)
    assert user.failed_login_attempts == 0