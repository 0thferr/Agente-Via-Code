"""Regressões encontradas na revisão de código do PR #1."""

from datetime import UTC, datetime, timedelta

import httpx

from app.integrations.email.oauth_clients import GmailClient, OutlookClient
from app.models.enums import MessageCategory
from app.services.communication.classifier import classify
from app.services.rate_limiter import RedisRateLimiter

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


# --- Prazos detectados por engano ------------------------------------------------------


def test_word_ending_in_ate_is_not_a_deadline_keyword():
    result = classify(
        "Interview invitation", "Dear candidate, your interview is on 25/11 at 14:00", NOW
    )

    assert result.deadline_at is None


def test_non_recruiting_email_never_has_deadline():
    result = classify("Promoção", "Oferta válida até 22/11", NOW)

    assert result.category is MessageCategory.NOT_RECRUITING
    assert result.deadline_at is None


def test_real_deadline_is_still_detected():
    result = classify("Teste técnico", "Entregar até 22/11", NOW)

    assert result.deadline_at == datetime(2026, 11, 22, 23, 59, tzinfo=UTC)


# --- Paginação do Gmail ----------------------------------------------------------------


def gmail_with_pages(pages: list[list[str]], detail_calls: list[str]) -> GmailClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            index = int(request.url.params.get("pageToken") or 0)
            body = {"messages": [{"id": message_id} for message_id in pages[index]]}
            if index + 1 < len(pages):
                body["nextPageToken"] = str(index + 1)
            return httpx.Response(200, json=body)
        message_id = request.url.path.rsplit("/", 1)[-1]
        detail_calls.append(message_id)
        return httpx.Response(
            200,
            json={
                "id": message_id,
                "snippet": "",
                "internalDate": str(int(NOW.timestamp() * 1000)),
                "payload": {"headers": []},
            },
        )

    return GmailClient(client_id="c", client_secret="s", transport=httpx.MockTransport(handler))


def test_gmail_follows_pages_and_skips_known_messages():
    details: list[str] = []
    client = gmail_with_pages([["m1", "m2"], ["m3", "m4"]], details)

    batch = client.list_messages("token", NOW - timedelta(days=1), limit=10, known_ids={"m2"})

    assert [m.provider_message_id for m in batch.messages] == ["m1", "m3", "m4"]
    assert details == ["m1", "m3", "m4"]  # mensagem já conhecida não é baixada de novo
    assert batch.has_more is False


def test_gmail_reports_more_when_limit_is_reached():
    client = gmail_with_pages([["m1", "m2"], ["m3"]], [])

    batch = client.list_messages("token", NOW - timedelta(days=1), limit=2, known_ids=set())

    assert len(batch.messages) == 2 and batch.has_more is True


# --- Rate limiter compatível com Redis < 7 ---------------------------------------------


class RecordingPipeline:
    def __init__(self, calls: list[tuple]) -> None:
        self.calls = calls

    def set(self, *args, **kwargs):
        self.calls.append(("set", args, kwargs))

    def incr(self, *args):
        self.calls.append(("incr", args, {}))

    def expire(self, *args, **kwargs):
        self.calls.append(("expire", args, kwargs))

    def execute(self):
        return [True, 1]


class RecordingRedis:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def pipeline(self):
        return RecordingPipeline(self.calls)


def test_rate_limiter_does_not_use_expire_nx():
    client = RecordingRedis()

    RedisRateLimiter(client).hit("login:x", limit=5, window_seconds=60)

    # EXPIRE ... NX só existe no Redis 7+; SET ... EX ... NX funciona em todas as versões.
    assert [name for name, _, _ in client.calls] == ["set", "incr"]
    assert client.calls[0][2] == {"ex": 60, "nx": True}


def test_outlook_follows_next_link_and_skips_known_messages():
    def item(message_id: str) -> dict:
        return {
            "id": message_id,
            "subject": "Vaga",
            "from": {"emailAddress": {"name": "RH", "address": "rh@x.example"}},
            "receivedDateTime": "2026-11-20T10:00:00Z",
        }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json={"value": [item("o3")]})
        next_link = "https://graph.microsoft.com/v1.0/me/messages?page=2"
        return httpx.Response(
            200, json={"value": [item("o1"), item("o2")], "@odata.nextLink": next_link}
        )

    client = OutlookClient(client_id="c", client_secret="s", transport=httpx.MockTransport(handler))

    batch = client.list_messages("token", NOW - timedelta(days=1), limit=10, known_ids={"o1"})

    assert [m.provider_message_id for m in batch.messages] == ["o2", "o3"]
    assert batch.has_more is False


# --- Analisador simulado: nomes curtos de tecnologia -----------------------------------


def test_short_technology_names_are_assessed_as_desirable():
    from app.services.analysis.requirements import KeywordRequirementAnalyzer
    from tests.factories import make_facts, make_job

    job = make_job(requirements=["FastAPI ou Django"], technologies=["Go", "FastAPI"])

    result = KeywordRequirementAnalyzer().analyze(job, make_facts())

    desirable = [item.requirement for item in result.requirements if not item.mandatory]
    # "go" aparece dentro de "django", mas Go é outra tecnologia e precisa ser avaliada.
    assert desirable == ["Go"]


# --- Segunda passada -------------------------------------------------------------------


def test_title_words_match_whole_words_only():
    from app.services.communication.matcher import Candidate, _score

    candidate = Candidate("a", "Empresa Zeta", "Engenheiro Java", "https://zeta.example/1")

    # Antes, "java" casava dentro de "javascript" (1 de 2 palavras = bônus do cargo).
    assert _score(candidate, "vaga engenharia javascript", "") == 0.0
    assert _score(candidate, "vaga engenheiro java", "") > 0


def test_summary_excerpt_never_cuts_a_word():
    from app.services.cover_letters.writers import MAX_SUMMARY_EXCERPT, _summary_excerpt

    summary = "palavra " * (MAX_SUMMARY_EXCERPT // 4)  # longo e sem ponto final

    excerpt = _summary_excerpt(summary)

    assert len(excerpt) <= MAX_SUMMARY_EXCERPT + 1
    assert excerpt.rstrip("….").split()[-1] == "palavra"
