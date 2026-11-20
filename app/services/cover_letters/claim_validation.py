"""Verificação de afirmações não comprovadas e do limite de caracteres."""

import re
from dataclasses import dataclass

from app.core.text_normalization import normalize_text
from app.models.job import JobOpportunity
from app.services.analysis.profile_facts import ProfileFacts
from app.services.cover_letters.writers import Claim

MAX_TERM_WORDS = 3
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


@dataclass(frozen=True)
class ValidatedText:
    content: str
    warnings: list[str]


def validate_cover_letter(
    content: str,
    claims: list[Claim],
    job: JobOpportunity,
    facts: ProfileFacts,
    max_characters: int,
) -> ValidatedText:
    warnings = _check_claims(claims, facts)
    warnings += unsupported_term_warnings(content, job, facts)
    content = content.strip()
    if len(content) > max_characters:
        content = truncate_at_sentence(content, max_characters)
        warnings.append(f"O texto excedeu {max_characters} caracteres e foi encurtado.")
    return ValidatedText(content=content, warnings=warnings)


def find_unsupported_terms(content: str, job: JobOpportunity, facts: ProfileFacts) -> list[str]:
    """Termos da vaga citados no texto que não aparecem em nenhum fato do perfil."""
    normalized_content = f" {normalize_text(content)} "
    known = [set(normalize_text(term).split()) for term in facts.known_terms()]
    candidates = [*job.technologies, *job.requirements]
    unsupported = []
    for term in dict.fromkeys(candidates):
        normalized = normalize_text(term)
        if not normalized or len(normalized.split()) > MAX_TERM_WORDS:
            continue
        mentioned = f" {normalized} " in normalized_content
        # Compara por palavras inteiras: "C" no perfil não pode "comprovar" "React".
        words = set(normalized.split())
        supported = any(item and (words <= item or item <= words) for item in known)
        if mentioned and not supported:
            unsupported.append(term)
    return unsupported


def truncate_at_sentence(content: str, limit: int) -> str:
    window = content[:limit]
    ends = [match.end() for match in _SENTENCE_END.finditer(window)]
    return window[: ends[-1]] if ends else window.rstrip()


def _check_claims(claims: list[Claim], facts: ProfileFacts) -> list[str]:
    valid_refs = set(facts.by_ref())
    return [
        f"Afirmação sem evidência no perfil: {claim.statement.strip()[:200]}"
        for claim in claims
        if not any(ref in valid_refs for ref in claim.evidence_refs)
    ]


def unsupported_term_warnings(content: str, job: JobOpportunity, facts: ProfileFacts) -> list[str]:
    return [
        f"O texto menciona '{term}', que não consta no perfil. Revise antes de usar."
        for term in find_unsupported_terms(content, job, facts)
    ]
