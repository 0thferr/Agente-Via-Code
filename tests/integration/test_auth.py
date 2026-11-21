from sqlalchemy import select

from app.models.audit import AuditLog
from tests.conftest import TEST_PASSWORD, login


def test_login_returns_token_pair(client, user):
    tokens = login(client, user.email)

    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"] and tokens["refresh_token"]


def test_login_is_case_insensitive_for_email(client, user):
    login(client, user.email.upper())


def test_login_with_wrong_password_returns_401(client, user):
    response = client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": "senha-errada-123"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_failed"


def test_login_with_unknown_email_returns_same_error(client):
    response = client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": TEST_PASSWORD}
    )

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "E-mail ou senha inválidos."


def test_login_is_rate_limited(client, user):
    payload = {"email": user.email, "password": "senha-errada-123"}
    statuses = [client.post("/api/v1/auth/login", json=payload).status_code for _ in range(6)]

    assert statuses[:5] == [401] * 5
    assert statuses[5] == 429


def test_me_requires_authentication(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_rejects_tampered_token(client, auth_headers):
    headers = {"Authorization": auth_headers["Authorization"] + "x"}

    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_me_returns_current_user(client, user, auth_headers):
    response = client.get("/api/v1/auth/me", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["email"] == user.email


def test_refresh_token_cannot_be_used_as_access_token(client, user):
    tokens = login(client, user.email)
    headers = {"Authorization": f"Bearer {tokens['refresh_token']}"}

    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_refresh_rotates_tokens(client, user):
    tokens = login(client, user.email)

    response = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert response.status_code == 200
    assert response.json()["refresh_token"] != tokens["refresh_token"]


def test_reusing_refresh_token_revokes_whole_family(client, db_session, user):
    first = login(client, user.email)
    second = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    ).json()

    reuse = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    after_reuse = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}
    )

    assert reuse.status_code == 401
    assert after_reuse.status_code == 401
    actions = db_session.scalars(select(AuditLog.action).where(AuditLog.user_id == user.id)).all()
    assert "auth.refresh_token_reuse" in actions


def test_audit_log_never_stores_password(client, db_session, user):
    login(client, user.email)

    details = db_session.scalars(select(AuditLog.details)).all()
    assert all(TEST_PASSWORD not in str(item) for item in details)
