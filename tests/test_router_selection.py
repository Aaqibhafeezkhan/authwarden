import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from authwarden import AuthWarden, MemoryUserStore, WardenConfig


def make_warden() -> AuthWarden:
    config = WardenConfig(secret_key="router-selection-secret-key-32b", require_email_verification=False)
    return AuthWarden(config=config, user_store=MemoryUserStore())


def make_app(router) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/auth")
    return app


def client_for(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def paths(app: FastAPI) -> set[str]:
    return set(app.openapi()["paths"])


def test_default_router_has_all_20_endpoints():
    assert len(paths(make_app(make_warden().build_router()))) == 20


def test_router_property_matches_default():
    warden = make_warden()
    assert paths(make_app(warden.router)) == paths(make_app(warden.build_router()))


def test_include_group():
    app = make_app(make_warden().build_router(include={"auth"}))
    assert len(paths(app)) == 12
    assert not any("/mfa/" in p or "/oauth/" in p for p in paths(app))


def test_include_single_endpoints():
    app = make_app(make_warden().build_router(include={"register", "login"}))
    assert paths(app) == {"/auth/register", "/auth/login"}


def test_include_group_and_endpoint_together():
    app = make_app(make_warden().build_router(include={"mfa", "login"}))
    assert paths(app) == {"/auth/login", "/auth/mfa/setup", "/auth/mfa/confirm", "/auth/mfa/disable"}


def test_exclude_groups():
    app = make_app(make_warden().build_router(exclude={"mfa", "oauth"}))
    assert len(paths(app)) == 12


def test_exclude_single_endpoint():
    app = make_app(make_warden().build_router(exclude={"register"}))
    assert "/auth/register" not in paths(app)
    assert len(paths(app)) == 19


@pytest.mark.asyncio
async def test_unmounted_endpoint_returns_404():
    app = make_app(make_warden().build_router(include={"login"}))
    async with client_for(app) as c:
        r = await c.post("/auth/register", json={"email": "a@x.com", "password": "strongpass123"})
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_mounted_endpoints_still_work():
    app = make_app(make_warden().build_router(include={"register", "login"}))
    async with client_for(app) as c:
        r = await c.post("/auth/register", json={"email": "a@x.com", "password": "strongpass123"})
        assert r.status_code == 201
        r = await c.post("/auth/login", json={"identifier": "a@x.com", "password": "strongpass123"})
        assert r.status_code == 200


def test_empty_include_mounts_nothing():
    assert paths(make_app(make_warden().build_router(include=set()))) == set()


def test_builds_are_independent():
    warden = make_warden()
    warden.build_router(include={"login"})
    assert len(paths(make_app(warden.build_router()))) == 20


@pytest.mark.parametrize("kwargs", [{"include": {"nope"}}, {"exclude": {"nope"}}, {"include": {"auth", "typo"}}])
def test_unknown_name_raises(kwargs):
    with pytest.raises(ValueError, match="Unknown group or endpoint"):
        make_warden().build_router(**kwargs)


def test_include_and_exclude_together_raises():
    with pytest.raises(ValueError, match="either include or exclude"):
        make_warden().build_router(include={"auth"}, exclude={"mfa"})