import pytest

from authwarden import AuthWarden, MemoryUserStore, UserCreate, WardenConfig
from authwarden.email.console import ConsoleEmailBackend
from authwarden.email.smtp import SmtpEmailBackend
from authwarden.flows.register import register_flow


def make_warden(**kwargs) -> AuthWarden:
    config = WardenConfig(secret_key="email-wiring-secret-key-32-bytes", **kwargs)
    return AuthWarden(config=config, user_store=MemoryUserStore())


@pytest.mark.asyncio
async def test_default_config_sends_email(capsys):
    warden = make_warden()
    await register_flow(
        UserCreate(email="user@example.com", password="strongpass123"),
        store=warden.store,
        config=warden.config,
        password_handler=warden.password_handler,
        notification_service=warden.notification_service,
    )
    assert "user@example.com" in capsys.readouterr().out


def test_smtp_config_selects_smtp_backend():
    warden = make_warden(email_backend="smtp")
    assert isinstance(warden.notification_service._email, SmtpEmailBackend)


def test_explicit_backend_is_used():
    backend = ConsoleEmailBackend()
    config = WardenConfig(secret_key="email-wiring-secret-key-32-bytes")
    warden = AuthWarden(config=config, user_store=MemoryUserStore(), email_backend=backend)
    assert warden.notification_service._email is backend