"""Descoberta, importação, normalização e deduplicação de vagas."""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.text_normalization import (
    canonicalize_url,
    clean_external_text,
    content_fingerprint,
    infer_work_model,
    sha256_hex,
)
from app.integrations.job_providers.base import (
    JobSearchQuery,
    ProviderCapability,
    ProviderError,
    RawJob,
)
from app.integrations.job_providers.registry import (
    MANUAL_PLATFORM_KEYS,
    MANUAL_PROVIDER_KEY,
    ProviderRegistry,
    detect_platform,
)
from app.models.enums import DiscoveryMethod
from app.models.job import JobOpportunity, JobSource
from app.schemas.job import (
    JobImportRequest,
    JobListFilters,
    JobSearchRequest,
    JobSearchResponse,
    JobSourceCreate,
    SourceSearchResult,
)
from app.services.audit_service import record_audit_event

logger = logging.getLogger(__name__)

MAX_TITLE_LENGTH = 300
MAX_SHORT_FIELD_LENGTH = 200
MAX_URL_LENGTH = 2000
MAX_LIST_ITEM_LENGTH = 100
MAX_REQUIREMENT_LENGTH = 300
MAX_LIST_ITEMS = 50
ALLOWED_URL_SCHEMES = ("http://", "https://")


class InvalidJobDataError(Exception):
    """Vaga recebida de uma fonte sem os dados mínimos (URL, cargo, empresa)."""


# --- Fontes -------------------------------------------------------------------------


def create_source(
    session: Session, registry: ProviderRegistry, user_id: uuid.UUID, data: JobSourceCreate
) -> JobSource:
    provider = registry.get(data.provider_key)
    if not provider.info.supports(ProviderCapability.SEARCH):
        raise ValidationError(
            f"{provider.info.name} não possui pesquisa automática; "
            "cadastre as vagas pela importação manual (POST /api/v1/jobs/import)."
        )
    exists = session.scalar(
        select(JobSource.id).where(
            JobSource.user_id == user_id, JobSource.provider_key == data.provider_key
        )
    )
    if exists:
        raise ConflictError("Esta fonte já está cadastrada.")

    source = JobSource(user_id=user_id, provider_key=data.provider_key, is_enabled=data.is_enabled)
    session.add(source)
    session.flush()
    record_audit_event(
        session,
        action="job_source.created",
        user_id=user_id,
        entity_type="job_source",
        entity_id=source.id,
        details={"provider_key": data.provider_key},
    )
    session.commit()
    return source


def list_sources(session: Session, user_id: uuid.UUID) -> list[JobSource]:
    return list(
        session.scalars(
            select(JobSource).where(JobSource.user_id == user_id).order_by(JobSource.created_at)
        )
    )


def set_source_enabled(
    session: Session, user_id: uuid.UUID, source_id: uuid.UUID, is_enabled: bool
) -> JobSource:
    source = _get_source(session, user_id, source_id)
    source.is_enabled = is_enabled
    record_audit_event(
        session,
        action="job_source.updated",
        user_id=user_id,
        entity_type="job_source",
        entity_id=source.id,
        details={"is_enabled": is_enabled},
    )
    session.commit()
    return source


# --- Descoberta ---------------------------------------------------------------------


def search_jobs(
    session: Session, registry: ProviderRegistry, user_id: uuid.UUID, request: JobSearchRequest
) -> JobSearchResponse:
    """Consulta as fontes e salva as vagas novas.

    Cada fonte é processada e confirmada separadamente: a falha de uma integração
    não desfaz nem impede o resultado das demais.
    """
    sources = _sources_to_search(session, user_id, request.source_ids)
    results: list[SourceSearchResult] = []
    created_ids: list[uuid.UUID] = []

    for source in sources:
        result = SourceSearchResult(source_id=source.id, provider_key=source.provider_key)
        results.append(result)
        query = _build_query(request, source)
        started_at = datetime.now(UTC)
        try:
            raw_jobs = registry.get(source.provider_key).search(query)
        except ProviderError as exc:
            logger.warning(
                "job_provider_search_failed",
                extra={"provider_key": source.provider_key, "error": str(exc)},
            )
            result.error = str(exc)
            continue

        result.fetched = len(raw_jobs)
        for raw in raw_jobs:
            try:
                job, created = save_job(
                    session,
                    user_id,
                    raw,
                    provider_key=source.provider_key,
                    source_id=source.id,
                    method=DiscoveryMethod.AUTOMATIC,
                )
            except InvalidJobDataError:
                result.skipped += 1
                continue
            if created:
                result.created += 1
                created_ids.append(job.id)
            else:
                result.duplicates += 1

        source.last_synced_at = started_at
        session.commit()

    record_audit_event(
        session,
        action="jobs.search_executed",
        user_id=user_id,
        details={"results": [result.model_dump(mode="json") for result in results]},
    )
    session.commit()
    return JobSearchResponse(results=results, created_job_ids=created_ids)


def import_job(
    session: Session, user_id: uuid.UUID, data: JobImportRequest
) -> tuple[JobOpportunity, bool]:
    """Cadastro manual de vaga. Idempotente: a mesma vaga devolve o registro existente."""
    platform = data.platform or detect_platform(data.url) or MANUAL_PROVIDER_KEY
    if platform not in MANUAL_PLATFORM_KEYS:
        raise ValidationError(
            f"Plataforma '{platform}' inválida para importação manual. "
            f"Use uma de: {', '.join(sorted(MANUAL_PLATFORM_KEYS))}."
        )
    raw = RawJob(**data.model_dump(exclude={"platform"}))
    job, created = save_job(
        session,
        user_id,
        raw,
        provider_key=platform,
        source_id=None,
        method=DiscoveryMethod.MANUAL,
    )
    if created:
        record_audit_event(
            session,
            action="job.imported",
            user_id=user_id,
            entity_type="job_opportunity",
            entity_id=job.id,
        )
    session.commit()
    return job, created


def save_job(
    session: Session,
    user_id: uuid.UUID,
    raw: RawJob,
    *,
    provider_key: str,
    source_id: uuid.UUID | None,
    method: DiscoveryMethod,
) -> tuple[JobOpportunity, bool]:
    """Normaliza e insere a vaga; se ela já existir, devolve a existente.

    Usa INSERT ... ON CONFLICT DO NOTHING: quem garante a unicidade é o banco, o que
    continua correto mesmo com duas buscas simultâneas (sem "verificar e depois
    inserir", que teria condição de corrida). Não faz commit.
    """
    values = _normalize(raw)
    statement = (
        insert(JobOpportunity)
        .values(
            user_id=user_id,
            source_id=source_id,
            provider_key=provider_key,
            discovery_method=method,
            **values,
        )
        .on_conflict_do_nothing()
        .returning(JobOpportunity.id)
    )
    new_id = session.scalar(statement)
    if new_id is not None:
        return session.get_one(JobOpportunity, new_id), True

    duplicate_keys = [
        JobOpportunity.url_hash == values["url_hash"],
        JobOpportunity.content_fingerprint == values["content_fingerprint"],
    ]
    # Sem esta verificação, "external_id == None" viraria "IS NULL" e casaria com
    # qualquer vaga da mesma fonte sem ID externo.
    if values["external_id"] is not None:
        duplicate_keys.append(
            (JobOpportunity.provider_key == provider_key)
            & (JobOpportunity.external_id == values["external_id"])
        )
    existing = session.scalar(
        select(JobOpportunity)
        .where(JobOpportunity.user_id == user_id, or_(*duplicate_keys))
        .limit(1)
    )
    if existing is None:
        raise ConflictError("Conflito ao salvar a vaga; tente novamente.")
    existing.last_seen_at = datetime.now(UTC)
    return existing, False


# --- Consulta -----------------------------------------------------------------------


def list_jobs(
    session: Session, user_id: uuid.UUID, filters: JobListFilters
) -> tuple[list[JobOpportunity], int]:
    conditions = [JobOpportunity.user_id == user_id]
    if filters.q:
        conditions.append(
            or_(
                JobOpportunity.title.icontains(filters.q, autoescape=True),
                JobOpportunity.company.icontains(filters.q, autoescape=True),
            )
        )
    if filters.company:
        conditions.append(JobOpportunity.company.icontains(filters.company, autoescape=True))
    if filters.provider_key:
        conditions.append(JobOpportunity.provider_key == filters.provider_key)
    if filters.work_model:
        conditions.append(JobOpportunity.work_model == filters.work_model)
    if filters.seniority:
        conditions.append(JobOpportunity.seniority == filters.seniority)
    if filters.posted_after:
        conditions.append(JobOpportunity.posted_at >= filters.posted_after)

    total = session.scalar(select(func.count()).select_from(JobOpportunity).where(*conditions))
    jobs = session.scalars(
        select(JobOpportunity)
        .where(*conditions)
        .order_by(JobOpportunity.posted_at.desc().nulls_last(), JobOpportunity.created_at.desc())
        .limit(filters.limit)
        .offset(filters.offset)
    )
    return list(jobs), total or 0


def get_job(session: Session, user_id: uuid.UUID, job_id: uuid.UUID) -> JobOpportunity:
    job = session.scalar(
        select(JobOpportunity).where(JobOpportunity.id == job_id, JobOpportunity.user_id == user_id)
    )
    if job is None:
        raise NotFoundError("Vaga não encontrada.")
    return job


# --- Auxiliares ---------------------------------------------------------------------


def _get_source(session: Session, user_id: uuid.UUID, source_id: uuid.UUID) -> JobSource:
    source = session.scalar(
        select(JobSource).where(JobSource.id == source_id, JobSource.user_id == user_id)
    )
    if source is None:
        raise NotFoundError("Fonte de vagas não encontrada.")
    return source


def _sources_to_search(
    session: Session, user_id: uuid.UUID, source_ids: list[uuid.UUID] | None
) -> list[JobSource]:
    if source_ids:
        sources = [_get_source(session, user_id, source_id) for source_id in source_ids]
        disabled = [str(source.id) for source in sources if not source.is_enabled]
        if disabled:
            raise ValidationError(f"Fontes desabilitadas: {', '.join(disabled)}.")
        return sources

    sources = [source for source in list_sources(session, user_id) if source.is_enabled]
    if not sources:
        raise ValidationError("Nenhuma fonte de vagas habilitada. Cadastre uma em /jobs/sources.")
    return sources


def _build_query(request: JobSearchRequest, source: JobSource) -> JobSearchQuery:
    posted_after = request.filters.posted_after
    if request.incremental and source.last_synced_at:
        posted_after = max(filter(None, [posted_after, source.last_synced_at]))
    return JobSearchQuery(
        **request.filters.model_dump(exclude={"posted_after"}), posted_after=posted_after
    )


def _normalize(raw: RawJob) -> dict:
    url = raw.url.strip()
    title = clean_external_text(raw.title, MAX_TITLE_LENGTH)
    company = clean_external_text(raw.company, MAX_SHORT_FIELD_LENGTH)
    if not url.lower().startswith(ALLOWED_URL_SCHEMES) or len(url) > MAX_URL_LENGTH:
        raise InvalidJobDataError("URL ausente ou inválida.")
    if not title or not company:
        raise InvalidJobDataError("Cargo e empresa são obrigatórios.")

    location = clean_external_text(raw.location, MAX_SHORT_FIELD_LENGTH) if raw.location else None
    canonical_url = canonicalize_url(url)
    return {
        "external_id": raw.external_id,
        "url": url,
        "canonical_url": canonical_url,
        "url_hash": sha256_hex(canonical_url),
        "content_fingerprint": content_fingerprint(company, title, location),
        "title": title,
        "company": company,
        "location": location or None,
        "work_model": raw.work_model or infer_work_model(location, title),
        "seniority": raw.seniority,
        "description": clean_external_text(raw.description),
        "requirements": _clean_list(raw.requirements, MAX_REQUIREMENT_LENGTH),
        "technologies": _clean_list(raw.technologies, MAX_LIST_ITEM_LENGTH),
        "salary_min": raw.salary_min,
        "salary_max": raw.salary_max,
        "salary_currency": raw.salary_currency,
        "posted_at": raw.posted_at,
    }


def _clean_list(items: list[str], max_item_length: int) -> list[str]:
    cleaned = (clean_external_text(item, max_item_length) for item in items[:MAX_LIST_ITEMS])
    return list(dict.fromkeys(item for item in cleaned if item))
