import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUserDep, SessionDep
from app.schemas.analysis import CoverLetterOut, CoverLetterRevisionRequest
from app.services.cover_letters import cover_letter_service

router = APIRouter(prefix="/cover-letters", tags=["cover-letters"])


@router.get("/{letter_id}", response_model=CoverLetterOut)
def get_cover_letter(
    letter_id: uuid.UUID, session: SessionDep, current_user: CurrentUserDep
) -> CoverLetterOut:
    letter = cover_letter_service.get_cover_letter(session, current_user.id, letter_id)
    return CoverLetterOut.model_validate(letter)


@router.post(
    "/{letter_id}/revisions", response_model=CoverLetterOut, status_code=status.HTTP_201_CREATED
)
def revise_cover_letter(
    letter_id: uuid.UUID,
    payload: CoverLetterRevisionRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> CoverLetterOut:
    """Salva o texto editado pelo usuário como nova versão (a anterior é preservada)."""
    letter = cover_letter_service.revise_cover_letter(
        session, current_user.id, letter_id, payload.content
    )
    return CoverLetterOut.model_validate(letter)
