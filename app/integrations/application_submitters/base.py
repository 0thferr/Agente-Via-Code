"""Contrato dos enviadores de candidatura (integrações de envio autorizadas).

Um enviador só existe para plataformas com integração de candidatura
expressamente permitida. Sem enviador, a candidatura segue o fluxo manual: o
usuário se candidata na plataforma e registra o envio no sistema.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.models.enums import ConfirmationSource


class SubmissionError(Exception):
    """A plataforma recusou ou não processou o envio (resultado conhecido: não enviado)."""


class SubmissionOutcomeUnknownError(Exception):
    """Não sabemos se o envio aconteceu (ex.: timeout depois de enviar os dados).

    Nunca tratamos esse caso como sucesso. Uma nova tentativa reutiliza a mesma
    chave de idempotência, para que a plataforma não crie uma segunda candidatura.
    """


@dataclass(frozen=True)
class FormQuestion:
    id: str
    label: str
    # Campo do perfil que responde a pergunta; None = só o usuário pode responder.
    profile_field: str | None = None


@dataclass(frozen=True)
class SubmissionPayload:
    job_external_id: str | None
    job_url: str
    candidate_name: str
    candidate_email: str | None
    cover_letter: str | None
    resume_storage_key: str | None
    answers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SubmissionResult:
    confirmation_id: str
    confirmation_source: ConfirmationSource


class ApplicationSubmitter(ABC):
    provider_key: str
    requires_resume: bool = True

    @abstractmethod
    def required_questions(self, job_external_id: str | None) -> list[FormQuestion]:
        """Perguntas obrigatórias do formulário de candidatura desta vaga."""

    @abstractmethod
    def submit(self, payload: SubmissionPayload, idempotency_key: str) -> SubmissionResult:
        """Envia a candidatura. Só retorna com confirmação verificável da plataforma."""
