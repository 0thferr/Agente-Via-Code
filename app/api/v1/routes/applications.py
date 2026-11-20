import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import (
    CoverLetterWriterDep,
    CurrentUserDep,
    RequirementAnalyzerDep,
    SessionDep,
    SubmitterRegistryDep,
)
from app.models.enums import ApplicationStatus
from app.schemas.application import (
    ApplicationAnswersRequest,
    ApplicationApproveRequest,
    ApplicationCreate,
    ApplicationEventOut,
    ApplicationListFilters,
    ApplicationListResponse,
    ApplicationOut,
    ApplicationPrepareRequest,
    ApplicationRejectRequest,
    ApplicationSettingsIn,
    ApplicationSettingsOut,
    MarkAppliedRequest,
    ProgressUpdateRequest,
)
from app.services.applications import application_service, submission_service

router = APIRouter(prefix="/applications", tags=["applications"])

# Rotas com caminho fixo vêm antes de /{application_id}.


@router.get("/settings", response_model=ApplicationSettingsOut)
def get_settings(session: SessionDep, current_user: CurrentUserDep) -> ApplicationSettingsOut:
    """Modo de candidatura (manual, supervised, automatic) e limites."""
    settings = application_service.get_settings(session, current_user.id)
    return ApplicationSettingsOut.model_validate(settings.model_dump())


@router.put("/settings", response_model=ApplicationSettingsOut)
def put_settings(
    payload: ApplicationSettingsIn, session: SessionDep, current_user: CurrentUserDep
) -> ApplicationSettingsOut:
    settings = application_service.save_settings(session, current_user.id, payload)
    return ApplicationSettingsOut.model_validate(settings.model_dump())


@router.post(
    "",
    response_model=ApplicationOut,
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_200_OK: {"description": "A candidatura da vaga já existia."}},
)
def create_application(
    payload: ApplicationCreate,
    response: Response,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> ApplicationOut:
    """Cria a candidatura de uma vaga (no máximo uma por vaga; chamada idempotente)."""
    application, created = application_service.create_application(
        session, current_user.id, payload.job_id
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return ApplicationOut.from_record(application)


@router.get("", response_model=ApplicationListResponse)
def list_applications(
    filters: Annotated[ApplicationListFilters, Query()],
    session: SessionDep,
    current_user: CurrentUserDep,
) -> ApplicationListResponse:
    items, total = application_service.list_applications(session, current_user.id, filters)
    return ApplicationListResponse(
        items=[ApplicationOut.from_record(item) for item in items],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


@router.get("/{application_id}", response_model=ApplicationOut)
def get_application(
    application_id: uuid.UUID, session: SessionDep, current_user: CurrentUserDep
) -> ApplicationOut:
    application = application_service.get_application(session, current_user.id, application_id)
    return ApplicationOut.from_record(application)


@router.post("/{application_id}/prepare", response_model=ApplicationOut)
def prepare_application(
    application_id: uuid.UUID,
    payload: ApplicationPrepareRequest,
    session: SessionDep,
    analyzer: RequirementAnalyzerDep,
    writer: CoverLetterWriterDep,
    submitters: SubmitterRegistryDep,
    current_user: CurrentUserDep,
) -> ApplicationOut:
    """Analisa a vaga e, se qualificada, monta texto, currículo e respostas para aprovação."""
    application = application_service.prepare_application(
        session, analyzer, writer, submitters, current_user.id, application_id, payload
    )
    return ApplicationOut.from_record(application)


@router.post("/{application_id}/answers", response_model=ApplicationOut)
def answer_questions(
    application_id: uuid.UUID,
    payload: ApplicationAnswersRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> ApplicationOut:
    """Responde perguntas obrigatórias do formulário que o perfil não cobre."""
    application = application_service.answer_questions(
        session, current_user.id, application_id, payload.answers
    )
    return ApplicationOut.from_record(application)


@router.post("/{application_id}/approve", response_model=ApplicationOut)
def approve_application(
    application_id: uuid.UUID,
    payload: ApplicationApproveRequest,
    session: SessionDep,
    submitters: SubmitterRegistryDep,
    current_user: CurrentUserDep,
) -> ApplicationOut:
    """Aprova o envio da versão exata do texto e do currículo revisados."""
    application = application_service.approve_application(
        session, submitters, current_user.id, application_id, payload
    )
    return ApplicationOut.from_record(application)


@router.post("/{application_id}/reject", response_model=ApplicationOut)
def reject_application(
    application_id: uuid.UUID,
    payload: ApplicationRejectRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> ApplicationOut:
    """Recusa a candidatura: ela é encerrada sem envio."""
    application = application_service.decline_application(
        session, current_user.id, application_id, payload.reason
    )
    return ApplicationOut.from_record(application)


@router.post("/{application_id}/submit", response_model=ApplicationOut)
def submit_application(
    application_id: uuid.UUID,
    session: SessionDep,
    submitters: SubmitterRegistryDep,
    current_user: CurrentUserDep,
) -> ApplicationOut:
    """Envia pela integração autorizada. Idempotente: repetir não gera novo envio."""
    application = submission_service.submit_application(
        session, submitters, current_user.id, application_id
    )
    return ApplicationOut.from_record(application)


@router.post("/{application_id}/mark-applied", response_model=ApplicationOut)
def mark_applied(
    application_id: uuid.UUID,
    payload: MarkAppliedRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> ApplicationOut:
    """Registra candidatura feita por você diretamente na plataforma."""
    application = application_service.mark_applied_manually(
        session, current_user.id, application_id, payload.confirmation, payload.applied_at
    )
    return ApplicationOut.from_record(application)


@router.post("/{application_id}/progress", response_model=ApplicationOut)
def update_progress(
    application_id: uuid.UUID,
    payload: ProgressUpdateRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> ApplicationOut:
    """Registra a etapa do processo seletivo (teste, entrevista, proposta...)."""
    application = application_service.update_progress(
        session, current_user.id, application_id, ApplicationStatus(payload.status), payload.note
    )
    return ApplicationOut.from_record(application)


@router.get("/{application_id}/events", response_model=list[ApplicationEventOut])
def list_events(
    application_id: uuid.UUID, session: SessionDep, current_user: CurrentUserDep
) -> list[ApplicationEventOut]:
    events = application_service.list_events(session, current_user.id, application_id)
    return [ApplicationEventOut.model_validate(event) for event in events]
