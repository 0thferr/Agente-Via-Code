import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUserDep, ProviderRegistryDep, SessionDep
from app.schemas.job import (
    JobDetailOut,
    JobImportRequest,
    JobImportResponse,
    JobListFilters,
    JobListResponse,
    JobSearchRequest,
    JobSearchResponse,
    JobSourceCreate,
    JobSourceOut,
    JobSourceUpdate,
    JobSummaryOut,
    ProviderOut,
)
from app.services import job_service

router = APIRouter(prefix="/jobs", tags=["jobs"])

# Rotas com caminho fixo (/providers, /sources...) vêm antes de /{job_id}.


@router.get("/providers", response_model=list[ProviderOut])
def list_providers(registry: ProviderRegistryDep, _: CurrentUserDep) -> list[ProviderOut]:
    """Catálogo de fontes, com capacidades e tipo de integração (automática, simulada, manual)."""
    return [
        ProviderOut(
            key=provider.info.key,
            name=provider.info.name,
            integration_status=provider.info.integration_status,
            capabilities=sorted(provider.info.capabilities),
            notes=provider.info.notes,
        )
        for provider in registry.all()
    ]


@router.get("/sources", response_model=list[JobSourceOut])
def list_sources(session: SessionDep, current_user: CurrentUserDep) -> list[JobSourceOut]:
    sources = job_service.list_sources(session, current_user.id)
    return [JobSourceOut.model_validate(source) for source in sources]


@router.post("/sources", response_model=JobSourceOut, status_code=status.HTTP_201_CREATED)
def create_source(
    payload: JobSourceCreate,
    session: SessionDep,
    registry: ProviderRegistryDep,
    current_user: CurrentUserDep,
) -> JobSourceOut:
    source = job_service.create_source(session, registry, current_user.id, payload)
    return JobSourceOut.model_validate(source)


@router.patch("/sources/{source_id}", response_model=JobSourceOut)
def update_source(
    source_id: uuid.UUID,
    payload: JobSourceUpdate,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> JobSourceOut:
    source = job_service.set_source_enabled(session, current_user.id, source_id, payload.is_enabled)
    return JobSourceOut.model_validate(source)


@router.post("/search", response_model=JobSearchResponse)
def search_jobs(
    payload: JobSearchRequest,
    session: SessionDep,
    registry: ProviderRegistryDep,
    current_user: CurrentUserDep,
) -> JobSearchResponse:
    """Pesquisa nas fontes habilitadas e salva só as vagas novas (sem duplicatas)."""
    return job_service.search_jobs(session, registry, current_user.id, payload)


@router.post(
    "/import",
    response_model=JobImportResponse,
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_200_OK: {"description": "A vaga já existia; nada foi criado."}},
)
def import_job(
    payload: JobImportRequest,
    response: Response,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> JobImportResponse:
    """Cadastra uma vaga manualmente a partir do link (fontes sem integração)."""
    job, created = job_service.import_job(session, current_user.id, payload)
    if not created:
        response.status_code = status.HTTP_200_OK
    return JobImportResponse(job=JobDetailOut.model_validate(job), created=created)


@router.get("", response_model=JobListResponse)
def list_jobs(
    filters: Annotated[JobListFilters, Query()],
    session: SessionDep,
    current_user: CurrentUserDep,
) -> JobListResponse:
    jobs, total = job_service.list_jobs(session, current_user.id, filters)
    return JobListResponse(
        items=[JobSummaryOut.model_validate(job) for job in jobs],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


@router.get("/{job_id}", response_model=JobDetailOut)
def get_job(job_id: uuid.UUID, session: SessionDep, current_user: CurrentUserDep) -> JobDetailOut:
    return JobDetailOut.model_validate(job_service.get_job(session, current_user.id, job_id))
