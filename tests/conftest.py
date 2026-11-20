"""Fixtures compartilhadas.

Os testes usam um PostgreSQL real (TEST_DATABASE_URL) porque o modelo depende
de recursos específicos dele (ARRAY, JSONB, constraints). Cada teste roda dentro
de uma transação que é desfeita ao final, então os testes não interferem entre si.
"""

import os
import tempfile
from collections.abc import Iterator

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://jobpilot:jobpilot@localhost:5432/jobpilot_test"
)
# Precisa vir antes de importar a aplicação, que lê as configurações na importação.
os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": TEST_DATABASE_URL,
        "JWT_SECRET_KEY": "test-secret-key-with-at-least-32-characters",
        "STORAGE_DIR": tempfile.mkdtemp(prefix="jobpilot-test-"),
        "LOG_LEVEL": "WARNING",
    }
)

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from alembic import command  # noqa: E402
from app.api import deps  # noqa: E402
from app.db.session import get_db_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import auth_service  # noqa: E402
from app.services.file_storage import LocalFileStorage  # noqa: E402
from app.services.rate_limiter import InMemoryRateLimiter  # noqa: E402

TEST_PASSWORD = "senha-forte-de-teste-123"


@pytest.fixture(scope="session")
def engine():
    engine = create_engine(TEST_DATABASE_URL)
    try:
        with engine.begin() as connection:
            # Recria o schema para garantir que as migrations rodam do zero.
            connection.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    except OperationalError as exc:
        pytest.exit(f"Banco de testes indisponível em TEST_DATABASE_URL: {exc}", returncode=2)

    alembic_config = Config("alembic.ini")
    alembic_config.attributes["database_url"] = TEST_DATABASE_URL
    command.upgrade(alembic_config, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(engine) -> Iterator[Session]:
    connection = engine.connect()
    transaction = connection.begin()
    # Os commits feitos pelo código viram savepoints dentro da transação externa.
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def storage(tmp_path) -> LocalFileStorage:
    return LocalFileStorage(tmp_path / "storage")


@pytest.fixture
def client(db_session: Session, storage: LocalFileStorage) -> Iterator[TestClient]:
    rate_limiter = InMemoryRateLimiter()
    app.dependency_overrides[get_db_session] = lambda: db_session
    app.dependency_overrides[deps.get_rate_limiter] = lambda: rate_limiter
    app.dependency_overrides[deps.get_file_storage] = lambda: storage
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def create_test_user(session: Session, email: str = "dev@example.com") -> User:
    return auth_service.create_user(session, email, TEST_PASSWORD)


@pytest.fixture
def user(db_session: Session) -> User:
    return create_test_user(db_session)


def login(client: TestClient, email: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": TEST_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def auth_headers(client: TestClient, user: User) -> dict[str, str]:
    tokens = login(client, user.email)
    return {"Authorization": f"Bearer {tokens['access_token']}"}
