from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_uow
from app.core.config import Settings
from app.domain.models import Subscriber
from app.main import create_app
from tests.fakes import FakeUnitOfWork, InMemoryStore

TEST_SECRET = "test-secret-for-unit-tests-only-0123456789"
TEST_ISSUER = "test-issuer"
OWNER = "5551000001"
OTHER = "5551000002"


@pytest.fixture
def settings() -> Settings:
    return Settings(auth_mode="local", jwt_secret=TEST_SECRET, jwt_issuer=TEST_ISSUER,
                    database_url="postgresql+psycopg://unused:unused@localhost:1/unused")


@pytest.fixture
def store() -> InMemoryStore:
    st = InMemoryStore()
    st.subscribers[OWNER] = Subscriber(OWNER, "Deniz Yilmaz")
    st.subscribers[OTHER] = Subscriber(OTHER, "Ece Kaya")
    return st


@pytest.fixture
def uow(store) -> FakeUnitOfWork:
    return FakeUnitOfWork(store)


@pytest.fixture
def client(settings, store) -> TestClient:
    app = create_app(settings)
    app.dependency_overrides[get_uow] = lambda: FakeUnitOfWork(store)
    return TestClient(app)


def make_token(username: str, groups: list[str], *, secret: str = TEST_SECRET, issuer: str = TEST_ISSUER,
               expires_in: int = 3600, token_use: str = "access") -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": f"sub-{username}",
        "username": username,
        "cognito:groups": groups,
        "token_use": token_use,
        "iss": issuer,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def auth(username: str, groups: list[str] | None = None, **kwargs) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(username, groups or ['subscriber'], **kwargs)}"}


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return auth("ops-admin", ["billing-admin"])


@pytest.fixture
def owner_headers() -> dict[str, str]:
    return auth(OWNER)


@pytest.fixture
def other_headers() -> dict[str, str]:
    return auth(OTHER)
