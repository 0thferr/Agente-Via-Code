from fastapi import APIRouter

from app.api.v1.routes import (
    ai,
    applications,
    auth,
    automations,
    cover_letters,
    integrations,
    jobs,
    messages,
    notifications,
    profile,
    system,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(profile.router)
api_router.include_router(jobs.router)
api_router.include_router(cover_letters.router)
api_router.include_router(ai.router)
api_router.include_router(applications.router)
api_router.include_router(automations.router)
api_router.include_router(integrations.router)
api_router.include_router(messages.router)
api_router.include_router(notifications.router)
