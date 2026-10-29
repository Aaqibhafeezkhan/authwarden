from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
  import sqlalchemy as sa
  from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
  from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
except ImportError as exc:
  raise ImportError(
    "SQLAlchemyUserStore requires SQLAlchemy. Install it with: pip install 'authwarden[sqlalchemy]'"
  )

from authwarden.models.user import OAuthAccount, UserInDB


class UTCDateTime(sa.TypeDecorator):
  impl = sa.DateTime(timezone=True)
  cache_ok = True

  def process_bind_param(self, value: datetime | None, dialect: sa.Dialect) -> datetime | None:
    if value is None:
      return None
    if value.tzinfo is None:
      value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

  def process_result_value(self, value: datetime | None, dialect: sa.Dialect) -> datetime | None:
    if value is not None and value.tzinfo is None:
      return value.replace(tzinfo=timezone.utc)
    return value


class Base(DeclarativeBase):
  pass


class UserTable(Base):
  __tablename__ = "authwarden_users"
  __table_args__ = (
    sa.Index("uq_authwarden_users_email", sa.func.lower(sa.Column("email")), unique=True),
    sa.Index("uq_authwarden_users_username", sa.func.lower(sa.Column("username")), unique=True),
  )

  id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
  email: Mapped[str] = mapped_column(sa.String(320))
  username: Mapped[str | None] = mapped_column(sa.String(255))
  full_name: Mapped[str | None] = mapped_column(sa.String(255))
  hashed_password: Mapped[str | None] = mapped_column(sa.String(255))
  phone_number: Mapped[str | None] = mapped_column(sa.String(32), unique=True)
  phone_verified: Mapped[bool] = mapped_column(sa.Boolean, default=False)
  is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
  is_verified: Mapped[bool] = mapped_column(sa.Boolean, default=False)
  is_superuser: Mapped[bool] = mapped_column(sa.Boolean, default=False)
  roles: Mapped[list[str]] = mapped_column(sa.JSON, default=list)
  scopes: Mapped[list[str]] = mapped_column(sa.JSON, default=list)
  extra_data: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
  mfa_enabled: Mapped[bool] = mapped_column(sa.Boolean, default=False)
  mfa_secret: Mapped[str | None] = mapped_column(sa.String(255))
  mfa_pending_secret: Mapped[str | None] = mapped_column(sa.String(255))
  backup_codes: Mapped[list[str]] = mapped_column(sa.JSON, default=list)
  last_verification_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
  verification_otp_hash: Mapped[str | None] = mapped_column(sa.String(255))
  verification_otp_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
  verification_otp_attempts: Mapped[int] = mapped_column(sa.Integer, default=0)
  last_reset_request_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
  reset_token_hash: Mapped[str | None] = mapped_column(sa.String(255))
  reset_token_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
  reset_otp_hash: Mapped[str | None] = mapped_column(sa.String(255))
  reset_otp_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
  reset_otp_attempts: Mapped[int] = mapped_column(sa.Integer, default=0)
  failed_login_attempts: Mapped[int] = mapped_column(sa.Integer, default=0)
  locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
  created_at: Mapped[datetime] = mapped_column(UTCDateTime)
  updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class OAuthAccountTable(Base):
  __tablename__ = "authwarden_oauth_accounts"
  __table_args__ = (sa.UniqueConstraint("provider", "provider_user_id"),)

  id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
  user_id: Mapped[str] = mapped_column(sa.ForeignKey("authwarden_users.id", ondelete="CASCADE"), index=True)
  provider: Mapped[str] = mapped_column(sa.String(32))
  provider_user_id: Mapped[str] = mapped_column(sa.String(255))
  email: Mapped[str | None] = mapped_column(sa.String(320))
  access_token: Mapped[str | None] = mapped_column(sa.Text)
  refresh_token: Mapped[str | None] = mapped_column(sa.Text)
  token_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
  created_at: Mapped[datetime] = mapped_column(UTCDateTime)
  updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


_USER_COLUMNS = frozenset(UserTable.__table__.columns.keys())
_ACCOUNT_COLUMNS = frozenset(OAuthAccountTable.__table__.columns.keys())


async def create_tables(engine: AsyncEngine) -> None:
  async with engine.begin() as connection:
    await connection.run_sync(Base.metadata.create_all)


class SQLAlchemyUserStore:
  def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
    self._session_factory = session_factory

  async def _one(self, statement: sa.Select) -> Any:
    async with self._session_factory() as session:
      return (await session.execute(statement)).scalar_one_or_none()

  @staticmethod
  def _user(row: UserTable | None) -> UserInDB | None:
    return UserInDB.model_validate(row, from_attributes=True) if row else None

  @staticmethod
  def _account(row: OAuthAccountTable | None) -> OAuthAccount | None:
    return OAuthAccount.model_validate(row, from_attributes=True) if row else None

  async def get_by_id(self, user_id: str) -> UserInDB | None:
    return self._user(await self._one(sa.select(UserTable).where(UserTable.id == user_id)))

  async def get_by_email(self, email: str) -> UserInDB | None:
    statement = sa.select(UserTable).where(sa.func.lower(UserTable.email) == email.lower())
    return self._user(await self._one(statement))

  async def get_by_username(self, username: str) -> UserInDB | None:
    statement = sa.select(UserTable).where(sa.func.lower(UserTable.username) == username.lower())
    return self._user(await self._one(statement))

  async def get_by_phone(self, phone: str) -> UserInDB | None:
    return self._user(await self._one(sa.select(UserTable).where(UserTable.phone_number == phone)))

  async def create(self, user: UserInDB) -> UserInDB:
    async with self._session_factory() as session:
      session.add(UserTable(**user.model_dump(include=_USER_COLUMNS)))
      await session.commit()
    return user

  async def update(self, user: UserInDB) -> UserInDB:
    async with self._session_factory() as session:
      await session.merge(UserTable(**user.model_dump(include=_USER_COLUMNS)))
      await session.commit()
    return user

  async def delete(self, user_id: str) -> None:
    async with self._session_factory() as session:
      await session.execute(sa.delete(OAuthAccountTable).where(OAuthAccountTable.user_id == user_id))
      await session.execute(sa.delete(UserTable).where(UserTable.id == user_id))
      await session.commit()

  async def get_oauth_account(self, provider: str, provider_user_id: str) -> OAuthAccount | None:
    statement = sa.select(OAuthAccountTable).where(
      OAuthAccountTable.provider == provider,
      OAuthAccountTable.provider_user_id == provider_user_id,
    )
    return self._account(await self._one(statement))

  async def get_oauth_accounts_for_user(self, user_id: str) -> list[OAuthAccount]:
    async with self._session_factory() as session:
      result = await session.execute(
        sa.select(OAuthAccountTable).where(OAuthAccountTable.user_id == user_id)
      )
      return [self._account(row) for row in result.scalars()]

  async def create_oauth_account(self, account: OAuthAccount) -> OAuthAccount:
    async with self._session_factory() as session:
      session.add(OAuthAccountTable(**account.model_dump(include=_ACCOUNT_COLUMNS)))
      await session.commit()
    return account

  async def update_oauth_account(self, account: OAuthAccount) -> OAuthAccount:
    async with self._session_factory() as session:
      await session.merge(OAuthAccountTable(**account.model_dump(include=_ACCOUNT_COLUMNS)))
      await session.commit()
    return account

  async def delete_oauth_account(self, user_id: str, provider: str) -> None:
    async with self._session_factory() as session:
      await session.execute(
        sa.delete(OAuthAccountTable).where(
          OAuthAccountTable.user_id == user_id,
          OAuthAccountTable.provider == provider,
        )
      )
      await session.commit()
