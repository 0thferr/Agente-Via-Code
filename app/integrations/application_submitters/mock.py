"""Enviador simulado, para as vagas do provedor simulado.

Imita uma plataforma que reconhece a chave de idempotência: reenviar com a
mesma chave devolve a mesma confirmação, sem criar uma segunda candidatura.
"""

import hashlib

from app.integrations.application_submitters.base import (
    ApplicationSubmitter,
    FormQuestion,
    SubmissionPayload,
    SubmissionResult,
)
from app.integrations.job_providers.mock import MOCK_PROVIDER_KEY
from app.models.enums import ConfirmationSource

COMMON_QUESTIONS = [
    FormQuestion(id="salary_expectation", label="Pretensão salarial", profile_field="salary"),
    FormQuestion(
        id="availability", label="Disponibilidade para início", profile_field="availability"
    ),
]
# Pergunta sem correspondência no perfil, para exercitar a intervenção humana.
EXTRA_QUESTIONS = {
    "mock-006": [FormQuestion(id="travel", label="Você tem disponibilidade para viagens?")],
}


class MockApplicationSubmitter(ApplicationSubmitter):
    provider_key = MOCK_PROVIDER_KEY

    def __init__(self, failure: Exception | None = None) -> None:
        self._failure = failure
        self._submissions: dict[str, SubmissionResult] = {}
        self.calls = 0

    def required_questions(self, job_external_id: str | None) -> list[FormQuestion]:
        return [*COMMON_QUESTIONS, *EXTRA_QUESTIONS.get(job_external_id or "", [])]

    def submit(self, payload: SubmissionPayload, idempotency_key: str) -> SubmissionResult:
        self.calls += 1
        if self._failure:
            raise self._failure
        if idempotency_key in self._submissions:
            return self._submissions[idempotency_key]
        digest = hashlib.sha256(idempotency_key.encode()).hexdigest()[:12].upper()
        result = SubmissionResult(
            confirmation_id=f"SIM-{digest}", confirmation_source=ConfirmationSource.SIMULATED
        )
        self._submissions[idempotency_key] = result
        return result
