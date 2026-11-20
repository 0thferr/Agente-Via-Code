import pytest

from app.integrations.application_submitters.base import SubmissionPayload
from app.integrations.application_submitters.mock import MockApplicationSubmitter
from app.models.enums import ApplicationStatus as S
from app.models.enums import ConfirmationSource
from app.services.applications.state_machine import ALLOWED_TRANSITIONS, can_transition


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.DISCOVERED, S.ANALYZING),
        (S.AWAITING_APPROVAL, S.READY_TO_APPLY),
        (S.READY_TO_APPLY, S.APPLYING),
        (S.APPLYING, S.APPLIED),
        (S.ERROR, S.APPLYING),
        (S.APPLIED, S.INTERVIEW),
        (S.OFFER, S.HIRED),
    ],
)
def test_allowed_transitions(current, target):
    assert can_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.DISCOVERED, S.APPLIED),  # não existe "enviado" sem passar pelo envio
        (S.AWAITING_APPROVAL, S.APPLYING),  # envio exige aprovação antes
        (S.QUALIFIED, S.APPLYING),
        (S.APPLYING, S.READY_TO_APPLY),
        (S.APPLIED, S.HIRED),
        (S.HIRED, S.CLOSED),
        (S.CLOSED, S.DISCOVERED),
    ],
)
def test_forbidden_transitions(current, target):
    assert not can_transition(current, target)


def test_every_status_has_transition_rules():
    assert set(ALLOWED_TRANSITIONS) == set(S)


def test_terminal_statuses_have_no_exit():
    assert ALLOWED_TRANSITIONS[S.HIRED] == ALLOWED_TRANSITIONS[S.CLOSED] == frozenset()


def test_mock_submitter_is_idempotent_by_key():
    submitter = MockApplicationSubmitter()
    payload = SubmissionPayload(
        job_external_id="mock-001",
        job_url="https://x.example",
        candidate_name="Ana",
        candidate_email=None,
        cover_letter="texto",
        resume_storage_key=None,
    )

    first = submitter.submit(payload, "key-1")
    again = submitter.submit(payload, "key-1")
    other = submitter.submit(payload, "key-2")

    assert first == again
    assert other.confirmation_id != first.confirmation_id
    assert first.confirmation_source is ConfirmationSource.SIMULATED
