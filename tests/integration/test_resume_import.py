from tests.conftest import create_test_user, login
from tests.integration.test_profile import VALID_PROFILE
from tests.pdf_factory import make_pdf

RESUME_LINES = [
    "Ana Souza",
    "ana.souza@example.com | +55 11 91234-5678",
    "linkedin.com/in/ana-souza github.com/anasouza",
    "Desenvolvedora Backend",
]


def upload(client, headers, content: bytes, content_type: str = "application/pdf"):
    files = {"file": ("curriculo.pdf", content, content_type)}
    return client.post("/api/v1/profile/resumes", files=files, headers=headers)


def test_upload_extracts_draft_without_touching_profile(client, auth_headers):
    response = upload(client, auth_headers, make_pdf(RESUME_LINES))

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["import_status"] == "pending_review"
    draft = body["extracted_data"]["profile_draft"]
    assert draft["contact_email"] == "ana.souza@example.com"
    assert draft["linkedin_url"] == "https://linkedin.com/in/ana-souza"
    assert draft["github_url"] == "https://github.com/anasouza"
    # Nada é salvo no perfil até o usuário confirmar.
    assert client.get("/api/v1/profile", headers=auth_headers).status_code == 404


def test_draft_does_not_invent_missing_fields(client, auth_headers):
    response = upload(client, auth_headers, make_pdf(["Ana Souza"]))

    assert response.json()["extracted_data"]["profile_draft"] == {}


def test_confirm_saves_reviewed_profile(client, auth_headers):
    resume_id = upload(client, auth_headers, make_pdf(RESUME_LINES)).json()["id"]

    confirmed = client.post(
        f"/api/v1/profile/resumes/{resume_id}/confirm",
        json={"profile": VALID_PROFILE},
        headers=auth_headers,
    )
    resume = client.get(f"/api/v1/profile/resumes/{resume_id}", headers=auth_headers).json()

    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["full_name"] == VALID_PROFILE["full_name"]
    assert resume["import_status"] == "confirmed"


def test_confirm_twice_is_rejected(client, auth_headers):
    resume_id = upload(client, auth_headers, make_pdf(RESUME_LINES)).json()["id"]
    url = f"/api/v1/profile/resumes/{resume_id}/confirm"
    client.post(url, json={"profile": VALID_PROFILE}, headers=auth_headers)

    response = client.post(url, json={"profile": VALID_PROFILE}, headers=auth_headers)

    assert response.status_code == 409


def test_rejects_non_pdf_content(client, auth_headers):
    response = upload(client, auth_headers, b"<html>not a pdf</html>")

    assert response.status_code == 422


def test_rejects_wrong_content_type(client, auth_headers):
    response = upload(client, auth_headers, make_pdf(RESUME_LINES), "text/plain")

    assert response.status_code == 422


def test_rejects_duplicated_upload(client, auth_headers):
    content = make_pdf(RESUME_LINES)
    upload(client, auth_headers, content)

    assert upload(client, auth_headers, content).status_code == 409


def test_corrupted_pdf_is_stored_as_failed(client, auth_headers):
    response = upload(client, auth_headers, b"%PDF-1.4\ncorrupted content")

    assert response.status_code == 201
    assert response.json()["import_status"] == "failed"


def test_download_returns_file_only_to_owner(client, db_session, auth_headers):
    content = make_pdf(RESUME_LINES)
    resume_id = upload(client, auth_headers, content).json()["id"]
    other = create_test_user(db_session, "other@example.com")
    other_headers = {"Authorization": f"Bearer {login(client, other.email)['access_token']}"}
    url = f"/api/v1/profile/resumes/{resume_id}/file"

    own = client.get(url, headers=auth_headers)
    foreign = client.get(url, headers=other_headers)

    assert own.status_code == 200
    assert own.content == content
    assert foreign.status_code == 404
