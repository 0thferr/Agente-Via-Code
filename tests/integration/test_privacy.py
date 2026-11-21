"""LGPD: exportação (acesso/portabilidade) e exclusão de conta (eliminação)."""

from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.core.crypto import SecretCipher
from app.main import app
from app.models.audit import AuditLog
from app.models.job import JobOpportunity
from app.models.profile import Resume
from app.models.user import User
from app.wiring import get_cipher
from tests.conftest import TEST_PASSWORD, create_test_user, login
from tests.integration.test_applications import ready_application

EXPORT = "/api/v1/privacy/export"
DELETION = "/api/v1/privacy/account-deletion"
CONFIRMATION = "EXCLUIR MINHA CONTA"


def full_account(client, headers):
    """Conta com perfil, currículo, vagas, análise, candidatura e webhook configurado."""
    application = ready_application(client, headers)
    app.dependency_overrides[get_cipher] = lambda: SecretCipher(Fernet.generate_key().decode())
    client.put(
        "/api/v1/notifications/preferences",
        json={"channels": ["webhook"], "webhook_url": "https://hooks.example.com/x"},
        headers=headers,
    )
    return application


def test_export_contains_all_user_data(client, auth_headers, user):
    application = full_account(client, auth_headers)

    response = client.get(EXPORT, headers=auth_headers)

    data = response.json()
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert data["account"]["email"] == user.email
    assert data["profile"][0]["full_name"] == "Ana Souza"
    assert len(data["skills"]) == 2 and len(data["resumes"]) == 1
    assert len(data["jobs"]) == 7
    assert data["applications"][0]["id"] == application["id"]
    assert data["application_events"] and data["cover_letters"] and data["job_analyses"]
    assert data["notification_preferences"][0]["webhook_url"] == "https://hooks.example.com/x"


def test_export_never_includes_secrets(client, auth_headers):
    full_account(client, auth_headers)

    body = client.get(EXPORT, headers=auth_headers).text

    for secret_field in ("password_hash", "encrypted_", "storage_key", "submission_key"):
        assert secret_field not in body
    assert TEST_PASSWORD not in body


def test_export_only_contains_own_data(client, db_session, auth_headers):
    full_account(client, auth_headers)
    other = create_test_user(db_session, "other@example.com")
    headers = {"Authorization": f"Bearer {login(client, other.email)['access_token']}"}

    data = client.get(EXPORT, headers=headers).json()

    assert data["account"]["email"] == "other@example.com"
    assert data["jobs"] == [] and data["applications"] == [] and data["profile"] == []


def test_export_is_audited(client, db_session, auth_headers, user):
    client.get(EXPORT, headers=auth_headers)

    actions = db_session.scalars(select(AuditLog.action).where(AuditLog.user_id == user.id)).all()
    assert "privacy.data_exported" in actions


def test_deletion_requires_password_and_exact_confirmation(client, auth_headers):
    wrong_password = client.post(
        DELETION,
        json={"password": "senha-errada", "confirmation": CONFIRMATION},
        headers=auth_headers,
    )
    wrong_phrase = client.post(
        DELETION, json={"password": TEST_PASSWORD, "confirmation": "sim"}, headers=auth_headers
    )

    assert wrong_password.status_code == 401
    assert wrong_phrase.status_code == 422
    assert client.get("/api/v1/auth/me", headers=auth_headers).status_code == 200


def test_deletion_removes_everything_but_keeps_anonymous_audit(
    client, db_session, auth_headers, user, storage
):
    full_account(client, auth_headers)
    resume_key = db_session.scalar(select(Resume.storage_key))
    assert storage.path_for(resume_key).exists()
    other = create_test_user(db_session, "other@example.com")

    response = client.post(
        DELETION,
        json={"password": TEST_PASSWORD, "confirmation": CONFIRMATION},
        headers=auth_headers,
    )

    assert response.status_code == 204
    assert db_session.get(User, user.id) is None
    assert db_session.scalar(select(func.count()).select_from(JobOpportunity)) == 0
    assert db_session.scalar(select(func.count()).select_from(Resume)) == 0
    assert not (storage._base_dir / resume_key).exists()  # PDF apagado do disco
    # Auditoria preservada, sem vínculo com a pessoa.
    assert (
        db_session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.user_id == user.id)
        )
        == 0
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "privacy.account_deleted")
        )
        == 1
    )
    # Outras contas não são afetadas, e a conta excluída não autentica mais.
    assert db_session.get(User, other.id) is not None
    assert client.get("/api/v1/auth/me", headers=auth_headers).status_code == 401
    relogin = client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
    )
    assert relogin.status_code == 401


def test_deletion_attempts_are_rate_limited(client, auth_headers):
    payload = {"password": "senha-errada", "confirmation": CONFIRMATION}
    statuses = [
        client.post(DELETION, json=payload, headers=auth_headers).status_code for _ in range(6)
    ]

    assert statuses[:5] == [401] * 5 and statuses[5] == 429
