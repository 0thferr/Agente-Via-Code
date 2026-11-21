import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile, status
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import CurrentUserDep, FileStorageDep, SessionDep, SettingsDep
from app.core.errors import NotFoundError, ValidationError
from app.schemas.profile import (
    ProfileIn,
    ProfileOut,
    ResumeImportConfirmation,
    ResumeOut,
    SkillOut,
)
from app.services import profile_service, resume_service
from app.services.resumes.parsing import ResumeParser
from app.wiring import get_resume_parser

router = APIRouter(prefix="/profile", tags=["profile"])

PDF_CONTENT_TYPE = "application/pdf"


@router.get("", response_model=ProfileOut)
def get_profile(session: SessionDep, current_user: CurrentUserDep) -> ProfileOut:
    return ProfileOut.model_validate(profile_service.get_profile(session, current_user.id))


@router.put("", response_model=ProfileOut)
def put_profile(
    payload: ProfileIn, session: SessionDep, current_user: CurrentUserDep
) -> ProfileOut:
    """Cria ou substitui o perfil completo (PUT é idempotente)."""
    profile = profile_service.save_profile(session, current_user.id, payload)
    return ProfileOut.model_validate(profile)


@router.get("/skills", response_model=list[SkillOut])
def list_skills(session: SessionDep, current_user: CurrentUserDep) -> list[SkillOut]:
    skills = profile_service.list_skills(session, current_user.id)
    return [SkillOut.model_validate(skill) for skill in skills]


@router.post("/resumes", response_model=ResumeOut, status_code=status.HTTP_201_CREATED)
async def upload_resume(
    file: UploadFile,
    session: SessionDep,
    settings: SettingsDep,
    storage: FileStorageDep,
    parser: Annotated[ResumeParser, Depends(get_resume_parser)],
    current_user: CurrentUserDep,
) -> ResumeOut:
    """Envia um currículo em PDF e devolve o rascunho estruturado para revisão."""
    if file.content_type != PDF_CONTENT_TYPE:
        raise ValidationError("Envie o currículo no formato PDF.")
    # Lê no máximo limite + 1 byte: suficiente para saber se o arquivo é grande demais
    # sem carregar arquivos enormes na memória.
    content = await file.read(settings.max_resume_size_bytes + 1)
    if len(content) > settings.max_resume_size_bytes:
        raise ValidationError(f"O PDF excede o limite de {settings.max_resume_size_mb} MB.")

    # O serviço é síncrono e pode chamar a IA (até dezenas de segundos): roda numa
    # thread para não travar o event loop e, com ele, as outras requisições.
    resume = await run_in_threadpool(
        resume_service.upload_resume,
        session,
        storage,
        current_user.id,
        file.filename or "curriculo.pdf",
        content,
        parser,
    )
    return ResumeOut.model_validate(resume)


@router.get("/resumes", response_model=list[ResumeOut])
def list_resumes(session: SessionDep, current_user: CurrentUserDep) -> list[ResumeOut]:
    return [
        ResumeOut.model_validate(r) for r in resume_service.list_resumes(session, current_user.id)
    ]


@router.get("/resumes/{resume_id}", response_model=ResumeOut)
def get_resume(
    resume_id: uuid.UUID, session: SessionDep, current_user: CurrentUserDep
) -> ResumeOut:
    return ResumeOut.model_validate(resume_service.get_resume(session, current_user.id, resume_id))


@router.get("/resumes/{resume_id}/file", response_class=FileResponse)
def download_resume(
    resume_id: uuid.UUID,
    session: SessionDep,
    storage: FileStorageDep,
    current_user: CurrentUserDep,
) -> FileResponse:
    resume = resume_service.get_resume(session, current_user.id, resume_id)
    try:
        path = storage.path_for(resume.storage_key)
    except FileNotFoundError as exc:
        raise NotFoundError("Arquivo do currículo não encontrado.") from exc
    return FileResponse(path, media_type=PDF_CONTENT_TYPE, filename=resume.original_filename)


@router.post("/resumes/{resume_id}/confirm", response_model=ProfileOut)
def confirm_resume_import(
    resume_id: uuid.UUID,
    payload: ResumeImportConfirmation,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> ProfileOut:
    """Salva no perfil os dados do currículo depois de revisados pelo usuário."""
    resume_service.confirm_import(session, current_user.id, resume_id, payload.profile)
    return ProfileOut.model_validate(profile_service.get_profile(session, current_user.id))
