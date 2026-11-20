"""Máquina de estados da candidatura.

Toda mudança de status passa por `transition`, que valida a transição contra a
tabela abaixo e registra o evento na linha do tempo. Assim nenhum caminho do
código consegue, por exemplo, marcar como APPLIED algo que nunca foi enviado.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.models.application import Application, ApplicationEvent
from app.models.enums import ApplicationStatus as S
from app.models.enums import EventActor

ALLOWED_TRANSITIONS: dict[S, frozenset[S]] = {
    S.DISCOVERED: frozenset({S.ANALYZING, S.CLOSED}),
    S.ANALYZING: frozenset({S.QUALIFIED, S.REJECTED, S.DISCOVERED}),
    S.REJECTED: frozenset({S.ANALYZING, S.CLOSED}),
    S.QUALIFIED: frozenset({S.AWAITING_APPROVAL, S.READY_TO_APPLY, S.CLOSED}),
    S.AWAITING_APPROVAL: frozenset({S.READY_TO_APPLY, S.APPLIED, S.CLOSED}),
    S.READY_TO_APPLY: frozenset({S.APPLYING, S.APPLIED, S.AWAITING_APPROVAL, S.CLOSED}),
    S.APPLYING: frozenset({S.APPLIED, S.ERROR}),
    S.ERROR: frozenset({S.APPLYING, S.APPLIED, S.CLOSED}),
    S.APPLIED: frozenset({S.ASSESSMENT, S.INTERVIEW, S.OFFER, S.CLOSED}),
    S.ASSESSMENT: frozenset({S.INTERVIEW, S.OFFER, S.CLOSED}),
    S.INTERVIEW: frozenset({S.ASSESSMENT, S.OFFER, S.CLOSED}),
    S.OFFER: frozenset({S.HIRED, S.CLOSED}),
    S.HIRED: frozenset(),
    S.CLOSED: frozenset(),
}

# Etapas do processo seletivo que o usuário (ou, na Fase 6, o monitor de e-mails)
# pode registrar depois do envio.
PROGRESS_STATUSES = frozenset({S.ASSESSMENT, S.INTERVIEW, S.OFFER, S.HIRED, S.CLOSED})


def can_transition(current: S, target: S) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def transition(
    session: Session,
    application: Application,
    target: S,
    *,
    actor: EventActor,
    event_type: str = "status_changed",
    details: dict[str, Any] | None = None,
) -> None:
    current = application.status
    if not can_transition(current, target):
        raise ConflictError(f"Transição inválida: {current} -> {target}.")
    application.status = target
    record_event(
        session,
        application,
        event_type,
        actor=actor,
        from_status=current,
        to_status=target,
        details=details,
    )


def record_event(
    session: Session,
    application: Application,
    event_type: str,
    *,
    actor: EventActor,
    from_status: S | None = None,
    to_status: S | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        ApplicationEvent(
            application_id=application.id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            details=details or {},
        )
    )
