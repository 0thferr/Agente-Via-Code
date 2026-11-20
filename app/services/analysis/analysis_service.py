"""Orquestra a análise de compatibilidade de uma vaga (mecanismo híbrido).

Ordem das etapas:
1. critérios determinísticos (salário, modalidade, localização, senioridade);
2. se algum critério eliminatório falhar, a vaga é descartada sem chamar a IA;
3. se a descrição for insuficiente, a análise fica pendente (nada é presumido);
4. análise de requisitos (IA ou simulada) e validação das evidências;
5. nota ponderada, confiança e recomendação calculadas em código.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.llm import AIProviderError
from app.ai.prompt_safety import detect_injection_signals
from app.core.errors import IntegrationError, NotFoundError, ValidationError
from app.models.analysis import AIPreferences, JobAnalysis
from app.models.enums import (
    AnalysisStatus,
    FitStatus,
    NotificationEvent,
    Recommendation,
    RequirementStatus,
)
from app.models.job import JobOpportunity
from app.schemas.analysis import AIPreferencesIn
from app.services import job_service, profile_service
from app.services.analysis import criteria as rules
from app.services.analysis.profile_facts import ProfileFacts, build_profile_facts
from app.services.analysis.requirements import (
    RequirementAnalyzer,
    ValidatedRequirements,
    validate_requirement_analysis,
)
from app.services.analysis.scoring import (
    LOCATION,
    SALARY,
    SENIORITY,
    SKILLS,
    WORK_MODEL,
    combine_scores,
    decide,
    requirements_score,
)
from app.services.audit_service import record_audit_event
from app.services.communication import notification_service

logger = logging.getLogger(__name__)

MIN_DESCRIPTION_LENGTH = 200
DETERMINISTIC_CONFIDENCE = 1.0
RULES_PROVIDER = "rules"
RULES_MODEL = "deterministic-v1"
STRONG_SKILLS_SCORE = 70
HIGH_MATCH_SCORE = 80
WEAK_SKILLS_SCORE = 40


# --- Preferências ---------------------------------------------------------------------


def get_preferences(session: Session, user_id: uuid.UUID) -> AIPreferencesIn:
    stored = session.get(AIPreferences, user_id)
    if stored is None:
        return AIPreferencesIn()
    return AIPreferencesIn.model_validate(stored, from_attributes=True)


def save_preferences(
    session: Session, user_id: uuid.UUID, data: AIPreferencesIn
) -> AIPreferencesIn:
    stored = session.get(AIPreferences, user_id) or AIPreferences(user_id=user_id)
    stored.min_score = data.min_score
    stored.weights = data.weights.model_dump()
    stored.cover_letter_tone = data.cover_letter_tone
    stored.writing_samples = list(data.writing_samples)
    session.add(stored)
    record_audit_event(
        session,
        action="ai_preferences.updated",
        user_id=user_id,
        details={"min_score": data.min_score, "weights": data.weights.model_dump()},
    )
    session.commit()
    return data


# --- Análise --------------------------------------------------------------------------


def analyze_job(
    session: Session, analyzer: RequirementAnalyzer, user_id: uuid.UUID, job_id: uuid.UUID
) -> JobAnalysis:
    job = job_service.get_job(session, user_id, job_id)
    try:
        profile = profile_service.get_profile(session, user_id)
    except NotFoundError as exc:
        raise ValidationError("Cadastre seu perfil profissional antes de analisar vagas.") from exc

    facts = build_profile_facts(profile)
    preferences = get_preferences(session, user_id)
    weights = preferences.weights.model_dump()
    criteria_results = {
        SALARY: rules.evaluate_salary(job, facts),
        WORK_MODEL: rules.evaluate_work_model(job, facts),
        LOCATION: rules.evaluate_location(job, facts),
        SENIORITY: rules.evaluate_seniority(job, facts),
    }

    analysis = JobAnalysis(
        user_id=user_id,
        job_id=job.id,
        min_score=preferences.min_score,
        weights=weights,
        profile_version=facts.profile_version,
        analyzer_provider=RULES_PROVIDER,
        analyzer_model=RULES_MODEL,
        warnings=[],
        requirements=[],
        decision_reasons=[],
    )
    injection_signals = detect_injection_signals(
        job.title, job.description, *job.requirements, *job.technologies
    )
    if injection_signals:
        analysis.warnings.append(
            "Possível tentativa de manipulação (prompt injection) no texto da vaga: "
            f"{', '.join(injection_signals)}. O conteúdo foi tratado apenas como dado."
        )

    eliminatory = [result for result in criteria_results.values() if result.eliminatory]
    if eliminatory:
        criteria_results[SKILLS] = rules.CriterionResult(
            None, FitStatus.UNKNOWN, "Não avaliado: a vaga falhou em critério eliminatório."
        )
        _finish(analysis, criteria_results, DETERMINISTIC_CONFIDENCE, bool(injection_signals))
    elif _has_insufficient_description(job):
        _mark_pending(
            analysis,
            criteria_results,
            "Descrição da vaga insuficiente para avaliar requisitos; nada foi presumido.",
        )
    else:
        _analyze_requirements(
            session, analyzer, job, facts, analysis, criteria_results, bool(injection_signals)
        )

    session.add(analysis)
    session.flush()
    _notify_high_match(session, analysis, job)
    record_audit_event(
        session,
        action="job.analyzed",
        user_id=user_id,
        entity_type="job_analysis",
        entity_id=analysis.id,
        details={
            "job_id": str(job.id),
            "status": analysis.status,
            "recommendation": analysis.recommendation,
            "score": analysis.score,
        },
    )
    session.commit()
    return analysis


def list_analyses(session: Session, user_id: uuid.UUID, job_id: uuid.UUID) -> list[JobAnalysis]:
    job_service.get_job(session, user_id, job_id)
    return list(
        session.scalars(
            select(JobAnalysis)
            .where(JobAnalysis.job_id == job_id, JobAnalysis.user_id == user_id)
            .order_by(JobAnalysis.created_at.desc())
        )
    )


def get_latest_analysis(
    session: Session, user_id: uuid.UUID, job_id: uuid.UUID
) -> JobAnalysis | None:
    return session.scalar(
        select(JobAnalysis)
        .where(
            JobAnalysis.job_id == job_id,
            JobAnalysis.user_id == user_id,
            JobAnalysis.status == AnalysisStatus.COMPLETED,
        )
        .order_by(JobAnalysis.created_at.desc())
        .limit(1)
    )


# --- Etapas internas ------------------------------------------------------------------


def _notify_high_match(session: Session, analysis: JobAnalysis, job: JobOpportunity) -> None:
    is_high_match = (
        analysis.recommendation is Recommendation.QUALIFIED
        and analysis.score is not None
        and analysis.score >= HIGH_MATCH_SCORE
    )
    if not is_high_match:
        return
    notification_service.notify(
        session,
        analysis.user_id,
        NotificationEvent.HIGH_MATCH_JOB,
        title=f"Vaga com alta compatibilidade ({analysis.score})",
        body=f"{job.title} — {job.company}",
        data={"job_id": str(job.id), "analysis_id": str(analysis.id)},
        dedup_key=f"high_match:{job.id}",
    )


def _analyze_requirements(
    session: Session,
    analyzer: RequirementAnalyzer,
    job: JobOpportunity,
    facts: ProfileFacts,
    analysis: JobAnalysis,
    criteria_results: dict[str, rules.CriterionResult],
    injection_detected: bool,
) -> None:
    analysis.analyzer_provider = analyzer.provider_name
    analysis.analyzer_model = analyzer.model_name
    try:
        raw = analyzer.analyze(job, facts)
    except AIProviderError as exc:
        _record_failure(session, analysis, criteria_results, str(exc))
        raise IntegrationError(f"Não foi possível concluir a análise: {exc}") from exc

    validated = validate_requirement_analysis(raw, facts)
    analysis.warnings.extend(validated.warnings)
    analysis.ai_summary = validated.summary or None
    analysis.requirements = _serialize_requirements(validated, facts)

    skills_score = requirements_score(validated.assessments)
    if skills_score is None:
        _mark_pending(
            analysis, criteria_results, "Nenhum requisito avaliável foi identificado na vaga."
        )
        return
    criteria_results[SKILLS] = _skills_criterion(skills_score, validated)
    _finish(analysis, criteria_results, validated.confidence, injection_detected)


def _finish(
    analysis: JobAnalysis,
    criteria_results: dict[str, rules.CriterionResult],
    analyzer_confidence: float,
    injection_detected: bool,
) -> None:
    result = combine_scores(
        {name: criterion.score for name, criterion in criteria_results.items()},
        analysis.weights,
        analyzer_confidence=analyzer_confidence,
        injection_detected=injection_detected,
    )
    eliminatory = [c for c in criteria_results.values() if c.eliminatory]
    recommendation = decide(result.score, analysis.min_score, bool(eliminatory))

    reasons = [f"Critério eliminatório: {criterion.reason}" for criterion in eliminatory]
    if result.score is not None and not eliminatory:
        comparison = ">=" if result.score >= analysis.min_score else "<"
        reasons.append(f"Nota {result.score} {comparison} mínimo configurado {analysis.min_score}.")
    unmet_mandatory = [
        item["requirement"]
        for item in analysis.requirements
        if item["mandatory"] and item["status"] == RequirementStatus.NOT_MET
    ]
    if unmet_mandatory:
        reasons.append(f"Requisitos obrigatórios não atendidos: {'; '.join(unmet_mandatory)}.")

    analysis.status = AnalysisStatus.COMPLETED
    analysis.score = result.score
    analysis.confidence = result.confidence
    analysis.recommendation = recommendation
    analysis.criteria = _serialize_criteria(criteria_results)
    analysis.decision_reasons = reasons


def _mark_pending(
    analysis: JobAnalysis, criteria_results: dict[str, rules.CriterionResult], reason: str
) -> None:
    criteria_results[SKILLS] = rules.CriterionResult(None, FitStatus.UNKNOWN, reason)
    analysis.status = AnalysisStatus.PENDING_INFORMATION
    analysis.recommendation = Recommendation.PENDING
    analysis.score = None
    analysis.confidence = 0.0
    analysis.criteria = _serialize_criteria(criteria_results)
    analysis.decision_reasons = [f"Análise pendente: {reason}"]


def _record_failure(
    session: Session,
    analysis: JobAnalysis,
    criteria_results: dict[str, rules.CriterionResult],
    error: str,
) -> None:
    """Persiste a falha para manter o histórico rastreável, mesmo sem resultado."""
    logger.warning("job_analysis_failed", extra={"job_id": str(analysis.job_id)})
    analysis.status = AnalysisStatus.FAILED
    analysis.recommendation = Recommendation.PENDING
    analysis.criteria = _serialize_criteria(criteria_results)
    analysis.error = error[:500]
    session.add(analysis)
    record_audit_event(
        session,
        action="job.analysis_failed",
        user_id=analysis.user_id,
        entity_type="job_opportunity",
        entity_id=analysis.job_id,
    )
    session.commit()


def _has_insufficient_description(job: JobOpportunity) -> bool:
    has_structured_requirements = bool(job.requirements or job.technologies)
    return len(job.description.strip()) < MIN_DESCRIPTION_LENGTH and not has_structured_requirements


def _skills_criterion(score: int, validated: ValidatedRequirements) -> rules.CriterionResult:
    assessed = [a for a in validated.assessments if a.status != RequirementStatus.UNCLEAR]
    met = [a for a in assessed if a.status == RequirementStatus.MET]
    if score >= STRONG_SKILLS_SCORE:
        fit = FitStatus.MATCH
    elif score >= WEAK_SKILLS_SCORE:
        fit = FitStatus.PARTIAL
    else:
        fit = FitStatus.MISMATCH
    return rules.CriterionResult(
        score, fit, f"{len(met)} de {len(assessed)} requisitos avaliados atendidos por completo."
    )


def _serialize_criteria(criteria_results: dict[str, rules.CriterionResult]) -> dict[str, dict]:
    return {
        name: {
            "score": result.score,
            "fit": result.fit,
            "reason": result.reason,
            "eliminatory": result.eliminatory,
        }
        for name, result in criteria_results.items()
    }


def _serialize_requirements(validated: ValidatedRequirements, facts: ProfileFacts) -> list[dict]:
    facts_by_ref = facts.by_ref()
    return [
        {
            "requirement": item.requirement,
            "mandatory": item.mandatory,
            "status": item.status,
            "evidence_refs": item.evidence_refs,
            "evidence": [facts_by_ref[ref].label for ref in item.evidence_refs],
        }
        for item in validated.assessments
    ]
