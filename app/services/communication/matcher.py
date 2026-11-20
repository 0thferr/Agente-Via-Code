"""Associação de e-mails a candidaturas.

Pontuação simples e explicável. Só associa automaticamente quando há um único
candidato claro; em caso de dúvida, pede validação do usuário.
"""

import email.utils
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.core.text_normalization import normalize_text

COMPANY_IN_TEXT_SCORE = 0.6
DOMAIN_MATCH_SCORE = 0.5
TITLE_WORDS_SCORE = 0.2
MATCH_THRESHOLD = 0.6
REVIEW_THRESHOLD = 0.3
MIN_LEAD_OVER_SECOND = 0.2
MIN_TITLE_WORD_LENGTH = 4


@dataclass(frozen=True)
class Candidate:
    application_id: object
    company: str
    title: str
    job_url: str


@dataclass(frozen=True)
class MatchResult:
    application_id: object | None
    confidence: float
    ambiguous: bool


def match_message(
    from_address: str, subject: str, snippet: str, candidates: list[Candidate]
) -> MatchResult:
    if not candidates:
        return MatchResult(None, 0.0, ambiguous=False)
    text = normalize_text(f"{from_address} {subject} {snippet}")
    sender_domain = _base_domain(email.utils.parseaddr(from_address)[1].rpartition("@")[2])
    scored = sorted(
        ((_score(candidate, text, sender_domain), candidate) for candidate in candidates),
        key=lambda pair: pair[0],
        reverse=True,
    )
    best_score, best = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    confidence = round(min(best_score, 1.0), 2)
    if best_score >= MATCH_THRESHOLD and best_score - second_score >= MIN_LEAD_OVER_SECOND:
        return MatchResult(best.application_id, confidence, ambiguous=False)
    if best_score >= REVIEW_THRESHOLD:
        return MatchResult(best.application_id, confidence, ambiguous=True)
    return MatchResult(None, confidence, ambiguous=False)


def _score(candidate: Candidate, text: str, sender_domain: str) -> float:
    score = 0.0
    company = normalize_text(candidate.company)
    if company and company in text:
        score += COMPANY_IN_TEXT_SCORE
    job_domain = _base_domain(urlsplit(candidate.job_url).hostname or "")
    if sender_domain and sender_domain == job_domain:
        score += DOMAIN_MATCH_SCORE
    title_words = [
        w for w in normalize_text(candidate.title).split() if len(w) >= MIN_TITLE_WORD_LENGTH
    ]
    text_words = set(text.split())
    if title_words and sum(word in text_words for word in title_words) >= len(title_words) / 2:
        score += TITLE_WORDS_SCORE
    return score


# Sufixos de segundo nível comuns: "empresa.com.br" deve virar "empresa.com.br",
# não "com.br" (que casaria com qualquer empresa brasileira).
SECOND_LEVEL_SUFFIXES = {"com.br", "net.br", "org.br", "gov.br", "co.uk", "com.au", "com.pt"}


def _base_domain(host: str) -> str:
    """Domínio registrável aproximado (lista curta de sufixos; não é a Public Suffix List)."""
    parts = host.lower().strip(".").split(".")
    if len(parts) < 2:
        return ""
    labels = 3 if ".".join(parts[-2:]) in SECOND_LEVEL_SUFFIXES else 2
    return ".".join(parts[-labels:]) if len(parts) >= labels else ""
