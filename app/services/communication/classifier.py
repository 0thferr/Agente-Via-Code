"""Classificação de e-mails de recrutamento por regras (sem IA) e extração de datas.

Regras explícitas são previsíveis, auditáveis e não enviam o conteúdo do
e-mail a terceiros. O e-mail é tratado como dado: nada no texto altera regras.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.text_normalization import normalize_text
from app.models.enums import MessageCategory

# Ordem importa: a primeira categoria encontrada vence (proposta > entrevista...).
CATEGORY_KEYWORDS: list[tuple[MessageCategory, tuple[str, ...]]] = [
    (MessageCategory.OFFER, ("proposta de emprego", "carta oferta", "job offer", "offer letter")),
    (
        MessageCategory.REJECTION,
        (
            "nao seguiremos",
            "optamos por outro",
            "nao foi selecionad",
            "unfortunately",
            "nao avancara",
        ),
    ),
    (
        MessageCategory.INTERVIEW_INVITE,
        ("entrevista", "interview", "bate papo", "conversa com o time"),
    ),
    (
        MessageCategory.ASSESSMENT,
        ("teste tecnico", "desafio tecnico", "case tecnico", "assessment", "coding challenge"),
    ),
    (
        MessageCategory.STAGE_CHANGE,
        ("proxima etapa", "avancou", "atualizacao sobre sua candidatura", "status da candidatura"),
    ),
]
RECRUITING_HINTS = ("candidatura", "vaga", "processo seletivo", "recrutamento", "application")
RESPONSE_HINTS = ("confirme", "confirmar", "disponibilidade", "responda", "please confirm", "?")
# Palavras inteiras (\b): "ate" não pode casar com "candidate" ou "update".
DEADLINE_PATTERN = re.compile(r"\b(prazo|ate|deadline|until)\b")

_NUMERIC_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_TIME = re.compile(r"\b(\d{1,2})(?:h|:)(\d{2})?\b")


@dataclass(frozen=True)
class Classification:
    category: MessageCategory
    requires_response: bool
    detected_dates: list[datetime]
    deadline_at: datetime | None


def classify(
    subject: str, snippet: str, received_at: datetime, timezone: str = "UTC"
) -> Classification:
    text = normalize_text(f"{subject} {snippet}")
    category = next(
        (cat for cat, words in CATEGORY_KEYWORDS if any(word in text for word in words)),
        None,
    )
    if category is None:
        category = (
            MessageCategory.OTHER_RECRUITING
            if any(hint in text for hint in RECRUITING_HINTS)
            else MessageCategory.NOT_RECRUITING
        )
    raw_text = f"{subject} {snippet}".lower()
    dates = extract_dates(f"{subject} {snippet}", received_at, timezone)
    has_deadline_word = bool(DEADLINE_PATTERN.search(text))
    is_recruiting = category is not MessageCategory.NOT_RECRUITING
    requires_response = category is not MessageCategory.NOT_RECRUITING and (
        category in {MessageCategory.INTERVIEW_INVITE, MessageCategory.OFFER}
        or any(hint in raw_text for hint in RESPONSE_HINTS)
    )
    return Classification(
        category=category,
        requires_response=requires_response,
        detected_dates=dates,
        deadline_at=_deadline(dates) if is_recruiting and has_deadline_word and dates else None,
    )


def extract_dates(text: str, reference: datetime, timezone: str = "UTC") -> list[datetime]:
    """Datas no formato dd/mm[/aaaa], com horário opcional logo depois ("às 14h").

    Datas sem ano assumem o próximo ano em que a data cai a partir da referência.
    Datas e horários são interpretados no fuso informado (o do usuário) e devolvidos
    em UTC.
    """
    zone = ZoneInfo(timezone)
    results = []
    for match in _NUMERIC_DATE.finditer(text):
        day, month, year = int(match.group(1)), int(match.group(2)), match.group(3)
        if not (1 <= day <= 31 and 1 <= month <= 12):
            continue
        full_year = _resolve_year(year, reference, month, day)
        moment = _time_after(text[match.end() : match.end() + 15])
        try:
            local = datetime.combine(datetime(full_year, month, day).date(), moment, zone)
            results.append(local.astimezone(UTC))
        except ValueError:
            continue
    return results


def _resolve_year(year: str | None, reference: datetime, month: int, day: int) -> int:
    if year:
        value = int(year)
        return value + 2000 if value < 100 else value
    candidate_year = reference.year
    try:
        if datetime(candidate_year, month, day, tzinfo=UTC) < reference - timedelta(days=1):
            candidate_year += 1
    except ValueError:
        pass
    return candidate_year


def _time_after(fragment: str) -> time:
    match = _TIME.search(fragment)
    if not match:
        return time(23, 59)  # sem horário: considera o fim do dia
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    return time(hour, minute) if hour < 24 and minute < 60 else time(23, 59)


def _deadline(dates: list[datetime]) -> datetime:
    return min(dates)
