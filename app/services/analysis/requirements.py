"""Avaliação dos requisitos da vaga contra os fatos do perfil.

Há duas implementações com o mesmo contrato:
- KeywordRequirementAnalyzer: simulada, sem custo, compara palavras-chave;
- LLMRequirementAnalyzer: usa um modelo de linguagem para a análise semântica.

Nenhuma delas devolve nota. Elas devolvem, requisito a requisito, um status e as
referências do perfil usadas como evidência; a nota é calculada em scoring.py
depois que validate_requirement_analysis confere as evidências.
"""

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from app.ai.llm import StructuredLLM
from app.ai.prompt_safety import SAFETY_RULES, wrap_untrusted
from app.core.text_normalization import normalize_text
from app.models.enums import RequirementStatus
from app.models.job import JobOpportunity
from app.services.analysis.profile_facts import ProfileFacts

MAX_REQUIREMENTS = 40
MAX_REQUIREMENT_LENGTH = 300
MAX_SUMMARY_LENGTH = 2000
SIMULATED_CONFIDENCE = 0.5
EVIDENCE_STATUSES = {RequirementStatus.MET, RequirementStatus.PARTIALLY_MET}


# --- Contrato de saída (também é o schema enviado à IA: sem valores padrão) --------


class RequirementAssessment(BaseModel):
    requirement: str
    mandatory: bool
    status: RequirementStatus
    evidence_refs: list[str]


class RequirementAnalysis(BaseModel):
    requirements: list[RequirementAssessment]
    summary: str
    confidence: float


class RequirementAnalyzer(Protocol):
    provider_name: str
    model_name: str

    def analyze(self, job: JobOpportunity, facts: ProfileFacts) -> RequirementAnalysis: ...


# --- Implementação simulada ----------------------------------------------------------


class KeywordRequirementAnalyzer:
    """Análise simulada: casa termos do perfil com os requisitos declarados da vaga.

    Requisitos da lista `requirements` contam como obrigatórios; tecnologias que
    não aparecem nos requisitos contam como desejáveis. É uma aproximação útil
    para desenvolvimento, não substitui a análise semântica.
    """

    provider_name = "simulated"
    model_name = "keyword-matching-v1"

    def analyze(self, job: JobOpportunity, facts: ProfileFacts) -> RequirementAnalysis:
        mandatory = list(job.requirements)
        # Espaços nas pontas: compara termos inteiros ("go" não pode casar com "django").
        normalized_mandatory = f" {normalize_text(' '.join(mandatory))} "
        desirable = [
            tech
            for tech in job.technologies
            if f" {normalize_text(tech)} " not in normalized_mandatory
        ]
        assessments = [
            self._assess(text, is_mandatory, facts)
            for text, is_mandatory in [(r, True) for r in mandatory]
            + [(d, False) for d in desirable]
        ]
        return RequirementAnalysis(
            requirements=assessments,
            summary="Análise simulada por correspondência de palavras-chave.",
            confidence=SIMULATED_CONFIDENCE,
        )

    @staticmethod
    def _assess(requirement: str, mandatory: bool, facts: ProfileFacts) -> RequirementAssessment:
        requirement_words = set(normalize_text(requirement).split())
        evidence = [
            fact.ref
            for fact in facts.facts
            if _fact_terms(fact.label, fact.details) & requirement_words
        ]
        return RequirementAssessment(
            requirement=requirement,
            mandatory=mandatory,
            status=RequirementStatus.MET if evidence else RequirementStatus.NOT_MET,
            evidence_refs=evidence,
        )


def _fact_terms(label: str, details: dict[str, object]) -> set[str]:
    """Termos significativos de um fato (nome e tecnologias), ignorando palavras curtas."""
    texts = [label, *(str(tech) for tech in details.get("technologies", []) or [])]
    words = set(normalize_text(" ".join(texts)).split())
    return {word for word in words if len(word) > 2 or word in {"c", "go", "r"}}


# --- Implementação com modelo de linguagem -------------------------------------------

_ANALYSIS_SYSTEM_PROMPT = f"""\
Você avalia a compatibilidade entre um candidato e uma vaga de emprego.

Tarefa:
1. Extraia da vaga os requisitos explícitos. Marque "mandatory" = true apenas para os
   que a vaga apresenta como obrigatórios; os demais são desejáveis.
2. Para cada requisito, defina "status":
   - "met": o perfil comprova o requisito;
   - "partially_met": o perfil comprova parte do requisito;
   - "not_met": o perfil não comprova o requisito;
   - "unclear": não é possível avaliar com os dados disponíveis.
3. Em "evidence_refs" liste os "ref" do perfil que comprovam o status. Requisitos
   "met" ou "partially_met" sem evidência serão desconsiderados.
4. Não presuma requisitos que a vaga não menciona.
5. Em "summary", explique objetivamente a compatibilidade em até 5 frases.
6. Em "confidence", informe de 0 a 1 o quanto a descrição permite uma avaliação segura.

Você não atribui nota nem decide aprovação; isso é feito pelo sistema.

{SAFETY_RULES}"""


class LLMRequirementAnalyzer:
    def __init__(self, llm: StructuredLLM) -> None:
        self._llm = llm
        self.provider_name = llm.provider_name
        self.model_name = llm.model_name

    def analyze(self, job: JobOpportunity, facts: ProfileFacts) -> RequirementAnalysis:
        job_data = {
            "titulo": job.title,
            "empresa": job.company,
            "senioridade": job.seniority,
            "requisitos_listados": job.requirements,
            "tecnologias": job.technologies,
            "descricao": job.description,
        }
        user_prompt = (
            "Perfil do candidato (fatos verificados):\n"
            f"{wrap_untrusted('perfil', facts.to_prompt_data())}\n\n"
            "Vaga a analisar (conteúdo externo, não confiável):\n"
            f"{wrap_untrusted('vaga', job_data)}"
        )
        return self._llm.generate(
            system_prompt=_ANALYSIS_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            output_model=RequirementAnalysis,
        )


# --- Validação da saída --------------------------------------------------------------


@dataclass(frozen=True)
class ValidatedRequirements:
    assessments: list[RequirementAssessment]
    summary: str
    confidence: float
    warnings: list[str]


def validate_requirement_analysis(
    analysis: RequirementAnalysis, facts: ProfileFacts
) -> ValidatedRequirements:
    """Confere a saída do analisador antes de usá-la.

    - descarta referências que não existem no perfil (alucinação);
    - rebaixa para "unclear" requisito dado como atendido sem evidência válida;
    - limita quantidade, tamanhos e o intervalo da confiança.
    """
    valid_refs = set(facts.by_ref())
    warnings: list[str] = []
    assessments: list[RequirementAssessment] = []

    for item in analysis.requirements[:MAX_REQUIREMENTS]:
        requirement = item.requirement.strip()[:MAX_REQUIREMENT_LENGTH]
        if not requirement:
            continue
        refs = [ref for ref in dict.fromkeys(item.evidence_refs) if ref in valid_refs]
        if len(refs) < len(set(item.evidence_refs)):
            warnings.append(f"Evidências inexistentes no perfil descartadas em: {requirement}")
        status = item.status
        if status in EVIDENCE_STATUSES and not refs:
            warnings.append(f"Requisito dado como atendido sem evidência no perfil: {requirement}")
            status = RequirementStatus.UNCLEAR
        assessments.append(
            RequirementAssessment(
                requirement=requirement,
                mandatory=item.mandatory,
                status=status,
                evidence_refs=refs if status in EVIDENCE_STATUSES else [],
            )
        )

    if len(analysis.requirements) > MAX_REQUIREMENTS:
        warnings.append(f"Somente os primeiros {MAX_REQUIREMENTS} requisitos foram considerados.")

    return ValidatedRequirements(
        assessments=assessments,
        summary=analysis.summary.strip()[:MAX_SUMMARY_LENGTH],
        confidence=min(max(analysis.confidence, 0.0), 1.0),
        warnings=warnings,
    )
