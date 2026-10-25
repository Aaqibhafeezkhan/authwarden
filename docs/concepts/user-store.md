# User Store (Database)

`AbstractUserStore` is a `Protocol`, not a base class — any object with the right async methods satisfies it. There's no inheritance required and no ORM lock-in.

```python
@runtime_checkable
class AbstractUserStore(Protocol):
    async def get_by_id(self, user_id: str) -> UserInDB | None: ...
    async def get_by_email(self, email: str) -> UserInDB | None: ...
    async def get_by_username(self, username: str) -> UserInDB | None: ...
    async def get_by_phone(self, phone: str) -> UserInDB | None: ...
    async def create(self, user: UserInDB) -> UserInDB: ...
    async def update(self, user: UserInDB) -> UserInDB: ...
    async def delete(self, user_id: str) -> None: ...

    async def get_oauth_account(self, provider: str, provider_user_id: str) -> OAuthAccount | None: ...
    async def get_oauth_accounts_for_user(self, user_id: str) -> list[OAuthAccount]: ...
    async def create_oauth_account(self, account: OAuthAccount) -> OAuthAccount: ...
    async def update_oauth_account(self, account: OAuthAccount) -> OAuthAccount: ...
    async def delete_oauth_account(self, user_id: str, provider: str) -> None: ...
```

`get_by_username`/`get_by_phone` only need real implementations if you actually use those fields (as login identifiers, or for SMS verification) — otherwise returning `None` unconditionally is fine.

`UserInDB` has `model_config = ConfigDict(from_attributes=True)`, so it can validate directly from an ORM row as long as field names line up — `UserInDB.model_validate(row)`.

## SQLAlchemy (async)

```python
from sqlalchemy import select
from authwarden import UserInDB
from authwarden.models.user import OAuthAccount

class SQLAlchemyUserStore:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def get_by_id(self, user_id: str) -> UserInDB | None:
        async with self.session_factory() as session:
            row = await session.get(UserModel, user_id)
            return UserInDB.model_validate(row) if row else None

    async def get_by_email(self, email: str) -> UserInDB | None:
        async with self.session_factory() as session:
            result = await session.execute(
                select(UserModel).where(UserModel.email == email)
            )
            row = result.scalar_one_or_none()
            return UserInDB.model_validate(row) if row else None

    async def get_by_username(self, username: str) -> UserInDB | None:
        async with self.session_factory() as session:
            result = await session.execute(
                select(UserModel).where(UserModel.username == username)
            )
            row = result.scalar_one_or_none()
            return UserInDB.model_validate(row) if row else None

    async def get_by_phone(self, phone: str) -> UserInDB | None:
        async with self.session_factory() as session:
            result = await session.execute(
                select(UserModel).where(UserModel.phone_number == phone)
            )
            row = result.scalar_one_or_none()
            return UserInDB.model_validate(row) if row else None

    async def create(self, user: UserInDB) -> UserInDB:
        async with self.session_factory() as session:
            row = UserModel(**user.model_dump())
            session.add(row)
            await session.commit()
            return user

    async def update(self, user: UserInDB) -> UserInDB:
        async with self.session_factory() as session:
            row = await session.get(UserModel, user.id)
            for key, value in user.model_dump().items():
                setattr(row, key, value)
            await session.commit()
            return user

    async def delete(self, user_id: str) -> None:
        async with self.session_factory() as session:
            row = await session.get(UserModel, user_id)
            if row:
                await session.delete(row)
                await session.commit()

    # OAuth account methods follow the same pattern against an OAuthAccountModel table
    ...
```

Your `UserModel` columns need to match `UserInDB`'s fields (or close enough for `model_validate` to map them) — `id`, `email`, `username`, `phone_number`, `hashed_password`, `is_active`, `is_verified`, `roles`, `scopes`, `mfa_secret`, `backup_codes`, and so on.

## MongoDB with Beanie

Beanie `Document` classes are Pydantic-based, so you can often skip the conversion step entirely:

```python
from beanie import Document
from authwarden import UserInDB

class UserDocument(UserInDB, Document):
    class Settings:
        name = "users"

class BeanieUserStore:
    async def get_by_id(self, user_id: str) -> UserInDB | None:
        return await UserDocument.get(user_id)

    async def get_by_email(self, email: str) -> UserInDB | None:
        return await UserDocument.find_one(UserDocument.email == email)

    async def create(self, user: UserInDB) -> UserInDB:
        doc = UserDocument(**user.model_dump())
        await doc.insert()
        return doc

    async def update(self, user: UserInDB) -> UserInDB:
        doc = await UserDocument.get(user.id)
        await doc.set(user.model_dump())
        return doc

    # ...
```

## SQLModel with SQLite

This example uses SQLModel with a real SQLite database. The store implements all 12 methods in `AbstractUserStore`; the methods are async to satisfy the protocol, while SQLModel's synchronous SQLite sessions perform the database I/O. For applications with heavier database concurrency, use SQLAlchemy's async engine/session pattern instead.

Install the extra dependencies:

```bash
pip install "sqlmodel>=0.0.22"
```

The example stores list/dictionary fields as JSON and keeps OAuth accounts in a separate table. Save the following as `sqlmodel_store.py`:

```python
from datetime import datetime

from sqlmodel import Field, Session, SQLModel, create_engine, select
from sqlalchemy import Column, JSON, func

from authwarden import OAuthAccount, UserInDB


class UserTable(SQLModel, table=True):
    __tablename__ = "users"

    id: str = Field(primary_key=True)
    email: str = Field(index=True)
    username: str | None = Field(default=None, index=True)
    full_name: str | None = None
    hashed_password: str | None = None
    phone_number: str | None = Field(default=None, index=True)
    phone_verified: bool = False
    is_active: bool = True
    is_verified: bool = False
    is_superuser: bool = False
    roles: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    scopes: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    extra_data: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    mfa_enabled: bool = False
    mfa_secret: str | None = None
    mfa_pending_secret: str | None = None
    backup_codes: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    last_verification_sent_at: datetime | None = None
    verification_otp_hash: str | None = None
    verification_otp_expires_at: datetime | None = None
    verification_otp_attempts: int = 0
    last_reset_request_at: datetime | None = None
    reset_token_hash: str | None = None
    reset_token_used_at: datetime | None = None
    reset_otp_hash: str | None = None
    reset_otp_expires_at: datetime | None = None
    reset_otp_attempts: int = 0
    failed_login_attempts: int = 0
    locked_until: datetime | None = None
    created_at: datetime
    updated_at: datetime


class OAuthAccountTable(SQLModel, table=True):
    __tablename__ = "oauth_accounts"

    id: str = Field(primary_key=True)
    user_id: str = Field(index=True)
    provider: str = Field(index=True)
    provider_user_id: str = Field(index=True)
    email: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    token_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

class SQLModelUserStore:
    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def _user(row):
        return UserInDB.model_validate(row) if row is not None else None

    @staticmethod
    def _account(row):
        return OAuthAccount.model_validate(row) if row is not None else None

    async def get_by_id(self, user_id: str) -> UserInDB | None:
        with Session(self.engine) as session:
            return self._user(session.get(UserTable, user_id))

    async def get_by_email(self, email: str) -> UserInDB | None:
        with Session(self.engine) as session:
            statement = select(UserTable).where(func.lower(UserTable.email) == email.lower())
            return self._user(session.exec(statement).one_or_none())

    async def get_by_username(self, username: str) -> UserInDB | None:
        with Session(self.engine) as session:
            statement = select(UserTable).where(func.lower(UserTable.username) == username.lower())
            return self._user(session.exec(statement).first())

    async def get_by_phone(self, phone: str) -> UserInDB | None:
        with Session(self.engine) as session:
            statement = select(UserTable).where(UserTable.phone_number == phone)
            return self._user(session.exec(statement).first())

    async def create(self, user: UserInDB) -> UserInDB:
        row = UserTable.model_validate(user.model_dump())
        with Session(self.engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._user(row)

    async def update(self, user: UserInDB) -> UserInDB:
        with Session(self.engine) as session:
            row = session.get(UserTable, user.id)
            if row is None:
                raise ValueError(f"User {user.id!r} does not exist")
            for key, value in user.model_dump().items():
                setattr(row, key, value)
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._user(row)

    async def delete(self, user_id: str) -> None:
        with Session(self.engine) as session:
            row = session.get(UserTable, user_id)
            if row is not None:
                session.delete(row)
                session.commit()
            statement = select(OAuthAccountTable).where(OAuthAccountTable.user_id == user_id)
            for account in session.exec(statement).all():
                session.delete(account)
            session.commit()

    async def get_oauth_account(
        self, provider: str, provider_user_id: str
    ) -> OAuthAccount | None:
        with Session(self.engine) as session:
            statement = select(OAuthAccountTable).where(
                OAuthAccountTable.provider == provider,
                OAuthAccountTable.provider_user_id == provider_user_id,
            )
            return self._account(session.exec(statement).first())

    async def get_oauth_accounts_for_user(self, user_id: str) -> list[OAuthAccount]:
        with Session(self.engine) as session:
            statement = select(OAuthAccountTable).where(OAuthAccountTable.user_id == user_id)
            return [OAuthAccount.model_validate(row) for row in session.exec(statement).all()]

    async def create_oauth_account(self, account: OAuthAccount) -> OAuthAccount:
        row = OAuthAccountTable.model_validate(account.model_dump())
        with Session(self.engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._account(row)

    async def update_oauth_account(self, account: OAuthAccount) -> OAuthAccount:
        with Session(self.engine) as session:
            row = session.get(OAuthAccountTable, account.id)
            if row is None:
                raise ValueError(f"OAuth account {account.id!r} does not exist")
            for key, value in account.model_dump().items():
                setattr(row, key, value)
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._account(row)

    async def delete_oauth_account(self, user_id: str, provider: str) -> None:
        with Session(self.engine) as session:
            statement = select(OAuthAccountTable).where(
                OAuthAccountTable.user_id == user_id,
                OAuthAccountTable.provider == provider,
            )
            for row in session.exec(statement).all():
                session.delete(row)
            session.commit()


engine = create_engine("sqlite:///./authwarden.db")
SQLModel.metadata.create_all(engine)
store = SQLModelUserStore(engine)
```

The JSON-backed fields are important: SQLite does not have native array columns, and the inherited Pydantic model contains list/dictionary values. The adapter converts rows back to `UserInDB`/`OAuthAccount` so callers receive the protocol's canonical models rather than ORM table objects. The user and OAuth tables should be migrated with your application's normal schema migration process instead of relying on `create_all` in production.

### Exercise the HTTP API

Use the same wiring as the [quickstart](../quickstart.md), replacing `MemoryUserStore` with the SQLModel store:

```python
from fastapi import Depends, FastAPI
from authwarden import AuthWarden, WardenConfig
from sqlmodel_store import store

warden = AuthWarden(
    config=WardenConfig(
        secret_key="replace-this-with-a-long-random-secret",
        require_email_verification=False,
    ),
    user_store=store,
)
app = FastAPI()
app.include_router(warden.router, prefix="/auth", tags=["auth"])

@app.get("/profile")
async def profile(user=Depends(warden.current_user)):
    return {"id": user.id, "email": user.email}
```

Start the app with `uvicorn main:app --reload`, then use `/docs` or the HTTP requests from the quickstart to register a user, log in, and call `/profile` with the returned bearer access token. Confirm the same user is still present after restarting the process; SQLite persists the record in `authwarden.db`.

**Limitations:** this deliberately small example uses synchronous SQLite sessions inside async protocol methods, so database calls block the event loop. It is suitable for demonstrating and testing the adapter, not a high-concurrency production deployment. Use an async database engine/session strategy for production workloads. Add migrations and uniqueness constraints appropriate to your application's registration policy before deploying.

## Tortoise ORM

Tortoise models aren't Pydantic-native, so map fields explicitly rather than relying on `model_validate`:

```python
from tortoise.models import Model
from tortoise import fields
from authwarden import UserInDB

class UserModel(Model):
    id = fields.CharField(pk=True, max_length=36)
    email = fields.CharField(max_length=255, unique=True)
    hashed_password = fields.CharField(max_length=255, null=True)
    is_active = fields.BooleanField(default=True)
    # ... remaining fields

class TortoiseUserStore:
    async def get_by_email(self, email: str) -> UserInDB | None:
        row = await UserModel.get_or_none(email=email)
        if row is None:
            return None
        return UserInDB(
            id=row.id, email=row.email, hashed_password=row.hashed_password,
            is_active=row.is_active,
            # ... remaining fields
        )
    # ...
```
