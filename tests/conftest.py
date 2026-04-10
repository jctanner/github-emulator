"""Shared test fixtures for the GitHub Emulator test suite."""

import asyncio
import hashlib
import os
import secrets
import tempfile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db

# API prefix constant for use in all test files
API = "/api/v3"


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_engine(tmp_path):
    """Create a fresh in-memory SQLite database for each test."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """Create a fresh database session for each test."""
    session_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def app(db_engine, tmp_path):
    """Create a test FastAPI application with overridden dependencies."""
    # Override settings before importing app
    os.environ["GITHUB_EMULATOR_DATA_DIR"] = str(tmp_path / "data")
    os.environ["GITHUB_EMULATOR_DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    os.environ["GITHUB_EMULATOR_BASE_URL"] = "http://testserver"

    os.makedirs(str(tmp_path / "data" / "repos"), exist_ok=True)

    from app.main import create_app
    from app.config import settings

    settings.DATA_DIR = str(tmp_path / "data")
    settings.DATABASE_URL = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    settings.BASE_URL = "http://testserver"

    test_app = create_app()

    # Override database dependency
    session_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_get_db():
        async with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_db] = override_get_db

    yield test_app


@pytest_asyncio.fixture
async def client(app):
    """Create an async HTTP test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest_asyncio.fixture
async def admin_user(db_session):
    """Create an admin user in the test database."""
    from app.models.user import User

    hashed = hashlib.sha256("admin".encode()).hexdigest()
    user = User(
        login="admin",
        hashed_password=hashed,
        name="Admin User",
        email="admin@test.com",
        site_admin=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_user(db_session):
    """Create a regular test user in the test database."""
    from app.models.user import User

    hashed = hashlib.sha256("password".encode()).hexdigest()
    user = User(
        login="testuser",
        hashed_password=hashed,
        name="Test User",
        email="test@test.com",
        site_admin=False,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_token(db_session, test_user):
    """Create a personal access token for the test user."""
    from app.models.token import PersonalAccessToken

    raw_token = f"ghp_{secrets.token_hex(20)}"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    pat = PersonalAccessToken(
        user_id=test_user.id,
        name="test-token",
        token_hash=token_hash,
        token_prefix=raw_token[:8],
        scopes=["repo", "user"],
    )
    db_session.add(pat)
    await db_session.commit()
    await db_session.refresh(pat)
    return raw_token


@pytest_asyncio.fixture
async def admin_token(db_session, admin_user):
    """Create a personal access token for the admin user."""
    from app.models.token import PersonalAccessToken

    raw_token = f"ghp_{secrets.token_hex(20)}"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    pat = PersonalAccessToken(
        user_id=admin_user.id,
        name="admin-token",
        token_hash=token_hash,
        token_prefix=raw_token[:8],
        scopes=["repo", "user", "admin:org"],
    )
    db_session.add(pat)
    await db_session.commit()
    await db_session.refresh(pat)
    return raw_token


def auth_headers(token: str) -> dict:
    """Build Authorization headers for a given token."""
    return {"Authorization": f"token {token}"}


@pytest_asyncio.fixture
async def test_repo_with_init(client, test_user, test_token):
    """Create a repo with auto_init=true so bare repo exists on disk.

    Returns (owner, repo_name, repo_data).
    """
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "init-repo", "auto_init": True},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    return ("testuser", "init-repo", data)
