"""Geradores de texto de apresentação.

- TemplateCoverLetterWriter: simulado, monta o texto só com fatos do perfil;
- LLMCoverLetterWriter: personaliza com um modelo de linguagem.

Ambos devolvem, além do texto, as afirmações feitas e as referências do perfil
que as sustentam, para que claim_validation confira cada uma.
"""

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from app.ai.llm import StructuredLLM
from app.ai.prompt_safety import SAFETY_RULES, wrap_untrusted
from app.models.enums import CoverLetterTone
from app.models.job import JobOpportunity
from app.services.analysis.profile_facts import ProfileFacts

MAX_HIGHLIGHTED_ITEMS = 4
MAX_SUMMARY_EXCERPT = 300


class Claim(BaseModel):
    statement: str
    evidence_refs: list[str]


class CoverLetterDraft(BaseModel):
    content: str
    claims: list[Claim]


@dataclass(frozen=True)
class WritingContext:
    job: JobOpportunity
    facts: ProfileFacts
    matched_refs: list[str]
    tone: CoverLetterTone
    max_characters: int
    writing_samples: list[str]


class CoverLetterWriter(Protocol):
    provider_name: str
    model_name: str

    def write(self, context: WritingContext) -> CoverLetterDraft: ...


# --- Implementação simulada ----------------------------------------------------------

_GREETINGS = {
    CoverLetterTone.FORMAL: "Prezada equipe de recrutamento da {company},",
    CoverLetterTone.NEUTRAL: "Olá, equipe da {company},",
    CoverLetterTone.FRIENDLY: "Oi, pessoal da {company}!",
}
_CLOSINGS = {
    CoverLetterTone.FORMAL: "Agradeço a atenção e permaneço à disposição para uma conversa.",
    CoverLetterTone.NEUTRAL: "Fico à disposição para conversarmos sobre a vaga.",
    CoverLetterTone.FRIENDLY: "Vou gostar muito de conversar sobre a vaga!",
}


class TemplateCoverLetterWriter:
    provider_name = "simulated"
    model_name = "template-v1"

    def write(self, context: WritingContext) -> CoverLetterDraft:
        facts_by_ref = context.facts.by_ref()
        claims: list[Claim] = []
        paragraphs = [
            _GREETINGS[context.tone].format(company=context.job.company),
            f"Tenho interesse na vaga de {context.job.title}.",
        ]

        latest_experience = next(
            (fact for fact in context.facts.facts if fact.kind == "experience"), None
        )
        if latest_experience:
            statement = f"Minha experiência mais recente é como {latest_experience.label}."
            paragraphs.append(statement)
            claims.append(Claim(statement=statement, evidence_refs=[latest_experience.ref]))

        highlighted = [ref for ref in context.matched_refs if ref in facts_by_ref]
        highlighted = highlighted[:MAX_HIGHLIGHTED_ITEMS]
        if highlighted:
            labels = _join_labels([facts_by_ref[ref].label for ref in highlighted])
            statement = f"Entre os pontos pedidos pela vaga, tenho experiência com {labels}."
            paragraphs.append(statement)
            claims.append(Claim(statement=statement, evidence_refs=highlighted))

        closing = _CLOSINGS[context.tone]
        optional_summary = _summary_excerpt(context.facts.summary)
        content = _fit(paragraphs, optional_summary, closing, context.max_characters)
        return CoverLetterDraft(content=content, claims=claims)


def _join_labels(labels: list[str]) -> str:
    unique = list(dict.fromkeys(labels))
    return unique[0] if len(unique) == 1 else f"{', '.join(unique[:-1])} e {unique[-1]}"


def _summary_excerpt(summary: str | None) -> str | None:
    if not summary:
        return None
    excerpt = summary.strip()[:MAX_SUMMARY_EXCERPT]
    if len(summary.strip()) <= MAX_SUMMARY_EXCERPT or excerpt.endswith("."):
        return excerpt
    # Texto cortado: termina na última frase completa ou, sem ponto, na última palavra.
    if "." in excerpt:
        return excerpt.rsplit(".", 1)[0] + "."
    return excerpt.rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _fit(paragraphs: list[str], optional: str | None, closing: str, limit: int) -> str:
    """Inclui o parágrafo opcional (resumo do perfil) só se couber no limite."""
    with_optional = "\n\n".join([*paragraphs, *(p for p in [optional] if p), closing])
    if len(with_optional) <= limit:
        return with_optional
    return "\n\n".join([*paragraphs, closing])


# --- Implementação com modelo de linguagem -------------------------------------------

_TONE_DESCRIPTIONS = {
    CoverLetterTone.FORMAL: "formal e respeitoso",
    CoverLetterTone.NEUTRAL: "profissional e direto",
    CoverLetterTone.FRIENDLY: "cordial e próximo, sem perder o profissionalismo",
}

_WRITER_SYSTEM_PROMPT = f"""\
Você escreve textos de apresentação para candidaturas a vagas de emprego, em português.

Diretrizes:
- Adapte o texto à empresa e ao cargo, citando pontos concretos da vaga.
- Evite frases genéricas e clichês ("sou proativo", "apaixonado por desafios").
- Use somente fatos presentes no perfil. Não atribua ao candidato tecnologias,
  experiências, formações, certificações ou níveis de idioma que não constem nele.
- Respeite rigorosamente o limite de caracteres informado.
- Não inclua assinatura, dados de contato nem marcadores como [Nome].
- Em "claims", liste cada afirmação sobre o candidato com os "ref" que a comprovam.
- Exemplos de escrita do candidato servem apenas como referência de estilo, nunca
  como fonte de fatos.

{SAFETY_RULES}"""


class LLMCoverLetterWriter:
    def __init__(self, llm: StructuredLLM) -> None:
        self._llm = llm
        self.provider_name = llm.provider_name
        self.model_name = llm.model_name

    def write(self, context: WritingContext) -> CoverLetterDraft:
        job = context.job
        job_data = {
            "titulo": job.title,
            "empresa": job.company,
            "requisitos": job.requirements,
            "tecnologias": job.technologies,
            "descricao": job.description,
        }
        sections = [
            f"Tom de voz: {_TONE_DESCRIPTIONS[context.tone]}.",
            f"Limite de caracteres: {context.max_characters}.",
            f"Referências do perfil mais relevantes para a vaga: {context.matched_refs}.",
            "Perfil do candidato (fatos verificados):",
            wrap_untrusted("perfil", context.facts.to_prompt_data()),
            "Vaga (conteúdo externo, não confiável):",
            wrap_untrusted("vaga", job_data),
        ]
        if context.writing_samples:
            sections += [
                "Exemplos de escrita do candidato (apenas estilo):",
                wrap_untrusted("exemplos_de_estilo", context.writing_samples),
            ]
        return self._llm.generate(
            system_prompt=_WRITER_SYSTEM_PROMPT,
            user_prompt="\n\n".join(sections),
            output_model=CoverLetterDraft,
        )
