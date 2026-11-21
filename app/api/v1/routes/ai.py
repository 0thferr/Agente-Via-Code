from fastapi import APIRouter

from app.api.deps import CurrentUserDep, SessionDep
from app.schemas.analysis import AIPreferencesIn, AIPreferencesOut
from app.services.analysis import analysis_service

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/preferences", response_model=AIPreferencesOut)
def get_preferences(session: SessionDep, current_user: CurrentUserDep) -> AIPreferencesOut:
    """Nota mínima, pesos da análise, tom de voz e exemplos de escrita (padrões se não salvos)."""
    preferences = analysis_service.get_preferences(session, current_user.id)
    return AIPreferencesOut.model_validate(preferences.model_dump())


@router.put("/preferences", response_model=AIPreferencesOut)
def put_preferences(
    payload: AIPreferencesIn, session: SessionDep, current_user: CurrentUserDep
) -> AIPreferencesOut:
    preferences = analysis_service.save_preferences(session, current_user.id, payload)
    return AIPreferencesOut.model_validate(preferences.model_dump())
