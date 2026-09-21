"""
Shared pytest fixtures.

Uses the same Postgres instance configured via DATABASE_URL — tests run
against a real Postgres (not SQLite) because several behaviors under
test (SELECT ... FOR UPDATE row locking, JSONB, native constraint
enforcement) do not have faithful SQLite equivalents. Master Plan §12
places concurrency tests as the highest-priority test category for
exactly this reason.

Note: this project pins to pytest-asyncio's config-based loop scoping
(`asyncio_default_fixture_loop_scope = "session"` in pyproject.toml)
rather than a custom `event_loop` fixture override, which pytest-asyncio
1.x deprecated in favor of that setting.
"""
import os
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, Base, engine
from app.core.security import hash_password
from app.models.enums import UserRole, UserStatus
from app.models.user import FarmerProfile, User
from tests.conftest_redis import fake_redis  # noqa: F401


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _setup_schema():
    """Create all tables once per test session, drop after. Refuses to run
    against anything but a local database, because the teardown drops
    every table and .env may point at the real Supabase project."""
    from sqlalchemy.engine import make_url

    host = make_url(str(engine.url)).host or ""
    if host not in ("localhost", "127.0.0.1", "::1") and os.environ.get("ALLOW_REMOTE_TEST_DB") != "1":
        pytest.exit(
            f"Refusing to run the test suite against non-local database host '{host}': "
            "the suite drops every table on teardown. Export DATABASE_URL pointing at a "
            "disposable local database, or set ALLOW_REMOTE_TEST_DB=1 for a disposable remote one.",
            returncode=2,
        )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client():
    import app.main as m

    transport = ASGITransport(app=m.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def active_farmer(db_session: AsyncSession) -> User:
    user = User(
        name="Fixture Farmer",
        phone=f"9{uuid.uuid4().int % 10**9:09d}",
        password_hash=hash_password("testpass123"),
        role=UserRole.FARMER,
        status=UserStatus.ACTIVE,
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(FarmerProfile(user_id=user.id))
    await db_session.commit()
    return user


@pytest_asyncio.fixture
async def super_admin(db_session: AsyncSession) -> User:
    user = User(
        name="Fixture Admin",
        phone=f"8{uuid.uuid4().int % 10**9:09d}",
        password_hash=hash_password("testpass123"),
        role=UserRole.SUPER_ADMIN,
        status=UserStatus.ACTIVE,
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest_asyncio.fixture
async def manager(db_session: AsyncSession) -> User:
    user = User(
        name="Fixture Manager",
        phone=f"7{uuid.uuid4().int % 10**9:09d}",
        password_hash=hash_password("testpass123"),
        role=UserRole.MANAGER,
        status=UserStatus.ACTIVE,
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest_asyncio.fixture
async def pending_farmer(db_session: AsyncSession) -> User:
    user = User(
        name="Pending Farmer",
        phone=f"6{uuid.uuid4().int % 10**9:09d}",
        password_hash=hash_password("testpass123"),
        role=UserRole.FARMER,
        status=UserStatus.PENDING,
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(FarmerProfile(user_id=user.id))
    await db_session.commit()
    return user


@pytest_asyncio.fixture
async def suspended_farmer(db_session: AsyncSession) -> User:
    user = User(
        name="Suspended Farmer",
        phone=f"5{uuid.uuid4().int % 10**9:09d}",
        password_hash=hash_password("testpass123"),
        role=UserRole.FARMER,
        status=UserStatus.SUSPENDED,
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(FarmerProfile(user_id=user.id))
    await db_session.commit()
    return user
