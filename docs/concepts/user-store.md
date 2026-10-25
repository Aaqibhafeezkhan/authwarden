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

## SQLAlchemy (built in)

```bash
pip install "authwarden[sqlalchemy]" asyncpg   # or aiosqlite for SQLite
```

Requires SQLAlchemy 2.0 or later in async mode. You install the database driver yourself.

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from authwarden import AuthWarden, WardenConfig
from authwarden.storage.sqlalchemy import SQLAlchemyUserStore, create_tables

engine = create_async_engine("postgresql+asyncpg://user:password@localhost/app")
session_factory = async_sessionmaker(engine, expire_on_commit=False)

warden = AuthWarden(
    config=WardenConfig(secret_key="change-me-to-a-real-32-byte-secret"),
    user_store=SQLAlchemyUserStore(session_factory),
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_tables(engine)
    yield
    await engine.dispose()


app = FastAPI(lifespan=lifespan)
app.include_router(warden.router, prefix="/auth", tags=["auth"])
```

What to know:

- **Tables.** `authwarden_users` and `authwarden_oauth_accounts`. The prefix keeps them away from your own `users` table. `create_tables(engine)` creates them if missing.
- **Migrations.** Import `Base` from `authwarden.storage.sqlalchemy` and include `Base.metadata` in your Alembic `target_metadata`. On PostgreSQL, autogenerate also creates the case-insensitive unique indexes on email and username. On SQLite, Alembic can't reflect those two indexes and skips them with a warning, so use `create_tables` or add them by hand.
- **Email and username** are matched case-insensitively, and uniqueness is enforced the same way by the database.
- **Datetimes** are stored in UTC and come back timezone-aware on every database, including SQLite.
- **Your own fields.** Put them in `extra_data`, which is stored as JSON. Fields you add by subclassing `UserInDB` are not persisted by this store. If you need real columns for them, write your own store.
- **Duplicates.** The register flow checks email, username and phone first, so the database constraint only triggers on a race. It then raises SQLAlchemy's `IntegrityError`.
- **Tested on** SQLite and PostgreSQL, with SQLAlchemy 2.0 and 2.1. Other databases may work but are not tested.

Everything below this point is for databases that don't have a built-in store.

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

## SQLModel

SQLModel classes are already Pydantic models, so the adapter is the thinnest of all three:

```python
from sqlmodel import SQLModel, Field, select
from authwarden import UserInDB

class UserTable(UserInDB, SQLModel, table=True):
    __tablename__ = "users"

class SQLModelUserStore:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def get_by_email(self, email: str) -> UserInDB | None:
        async with self.session_factory() as session:
            result = await session.execute(select(UserTable).where(UserTable.email == email))
            return result.scalar_one_or_none()
    # ...
```

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