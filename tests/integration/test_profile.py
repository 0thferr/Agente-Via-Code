import copy

import pytest

from tests.conftest import create_test_user, login

VALID_PROFILE = {
    "full_name": "Ana Souza",
    "contact_email": "ana@example.com",
    "phone": "+55 11 91234-5678",
    "city": "São Paulo",
    "country": "Brasil",
    "current_title": "Desenvolvedora Backend Júnior",
    "desired_titles": ["Desenvolvedora Backend", "Desenvolvedora Python"],
    "summary": "Desenvolvedora focada em APIs com Python.",
    "github_url": "https://github.com/ana",
    "salary_expectation_min": "5000.00",
    "salary_currency": "BRL",
    "desired_work_models": ["remote", "hybrid"],
    "accepted_locations": ["São Paulo"],
    "experiences": [
        {
            "company": "Empresa X",
            "title": "Desenvolvedora Júnior",
            "start_date": "2024-02-01",
            "is_current": True,
            "technologies": ["Python", "FastAPI"],
        }
    ],
    "educations": [
        {
            "institution": "Universidade Y",
            "degree": "Bacharelado",
            "field_of_study": "Ciência da Computação",
            "status": "completed",
            "start_date": "2019-02-01",
            "end_date": "2023-12-15",
        }
    ],
    "skills": [
        {"name": "Python", "category": "technology", "level": "intermediate"},
        {"name": "Comunicação", "category": "behavioral"},
    ],
    "languages": [{"name": "Inglês", "proficiency": "intermediate"}],
    "certifications": [],
}


def test_get_profile_before_creation_returns_404(client, auth_headers):
    assert client.get("/api/v1/profile", headers=auth_headers).status_code == 404


def test_put_creates_and_get_returns_profile(client, auth_headers):
    created = client.put("/api/v1/profile", json=VALID_PROFILE, headers=auth_headers)
    fetched = client.get("/api/v1/profile", headers=auth_headers)

    assert created.status_code == 200, created.text
    assert fetched.json()["full_name"] == "Ana Souza"
    assert fetched.json()["experiences"][0]["technologies"] == ["Python", "FastAPI"]
    assert len(fetched.json()["skills"]) == 2


def test_put_replaces_nested_lists(client, auth_headers):
    client.put("/api/v1/profile", json=VALID_PROFILE, headers=auth_headers)
    updated = copy.deepcopy(VALID_PROFILE)
    updated["skills"] = [{"name": "Python", "category": "technology", "level": "advanced"}]

    response = client.put("/api/v1/profile", json=updated, headers=auth_headers)

    assert response.status_code == 200
    assert [skill["level"] for skill in response.json()["skills"]] == ["advanced"]


def test_put_is_idempotent(client, auth_headers):
    first = client.put("/api/v1/profile", json=VALID_PROFILE, headers=auth_headers).json()
    second = client.put("/api/v1/profile", json=VALID_PROFILE, headers=auth_headers).json()

    assert first["id"] == second["id"]
    assert len(second["experiences"]) == 1


def test_list_skills(client, auth_headers):
    client.put("/api/v1/profile", json=VALID_PROFILE, headers=auth_headers)

    response = client.get("/api/v1/profile/skills", headers=auth_headers)

    assert {skill["name"] for skill in response.json()} == {"Python", "Comunicação"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("full_name", ""),
        ("salary_currency", "real"),
        ("github_url", "not-a-url"),
        ("desired_work_models", ["anywhere"]),
        ("phone", "abc"),
    ],
)
def test_put_rejects_invalid_fields(client, auth_headers, field, value):
    payload = {**VALID_PROFILE, field: value}

    assert client.put("/api/v1/profile", json=payload, headers=auth_headers).status_code == 422


def test_put_rejects_experience_ending_before_start(client, auth_headers):
    payload = copy.deepcopy(VALID_PROFILE)
    payload["experiences"][0].update(
        {"is_current": False, "start_date": "2024-02-01", "end_date": "2023-01-01"}
    )

    assert client.put("/api/v1/profile", json=payload, headers=auth_headers).status_code == 422


def test_put_rejects_duplicated_skills(client, auth_headers):
    payload = copy.deepcopy(VALID_PROFILE)
    payload["skills"].append({"name": "python", "category": "technology"})

    assert client.put("/api/v1/profile", json=payload, headers=auth_headers).status_code == 422


def test_salary_requires_currency(client, auth_headers):
    payload = {**VALID_PROFILE, "salary_currency": None}

    assert client.put("/api/v1/profile", json=payload, headers=auth_headers).status_code == 422


def test_users_cannot_see_each_others_profile(client, db_session, auth_headers):
    client.put("/api/v1/profile", json=VALID_PROFILE, headers=auth_headers)
    other = create_test_user(db_session, "other@example.com")
    other_headers = {"Authorization": f"Bearer {login(client, other.email)['access_token']}"}

    assert client.get("/api/v1/profile", headers=other_headers).status_code == 404
