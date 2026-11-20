"""Caixa de entrada simulada, para demonstrar o monitoramento sem OAuth real.

As mensagens citam as empresas fictícias do provedor de vagas simulado.
"""

from datetime import UTC, datetime, timedelta

from app.integrations.email.base import EmailClient, EmailMessage
from app.models.enums import EmailProvider

SIMULATED_ADDRESS = "caixa-simulada@jobpilot.example"


class SimulatedEmailClient(EmailClient):
    provider = EmailProvider.SIMULATED
    uses_oauth = False

    def __init__(self, messages: list[EmailMessage] | None = None) -> None:
        self._messages = messages

    def account_email(self, access_token: str) -> str:
        return SIMULATED_ADDRESS

    def list_messages(self, access_token: str, since: datetime, limit: int) -> list[EmailMessage]:
        messages = self._messages if self._messages is not None else default_messages()
        return [message for message in messages if message.received_at >= since][:limit]


def default_messages(now: datetime | None = None) -> list[EmailMessage]:
    now = now or datetime.now(UTC)
    interview_day = (now + timedelta(days=3)).strftime("%d/%m/%Y")
    deadline_day = (now + timedelta(days=1)).strftime("%d/%m/%Y")
    return [
        EmailMessage(
            provider_message_id="sim-001",
            thread_id="t-001",
            from_address="Recrutamento Acme <talentos@acme-tech.example>",
            subject="Convite para entrevista - Desenvolvedora Backend Python Júnior",
            snippet=f"Olá! Gostaríamos de agendar uma entrevista no dia {interview_day} às 14h. "
            "Pode confirmar sua disponibilidade?",
            received_at=now - timedelta(hours=2),
        ),
        EmailMessage(
            provider_message_id="sim-002",
            thread_id="t-002",
            from_address="Fintech Zeta <jobs@fintech-zeta.example>",
            subject="Teste técnico - Backend Python Sênior",
            snippet=f"Segue o link do desafio técnico. Prazo para entrega: até {deadline_day}.",
            received_at=now - timedelta(hours=5),
        ),
        EmailMessage(
            provider_message_id="sim-003",
            thread_id="t-003",
            from_address="Newsletter Tech <news@newsletter.example>",
            subject="As 10 linguagens mais usadas em 2026",
            snippet="Confira o ranking desta semana.",
            received_at=now - timedelta(hours=8),
        ),
        EmailMessage(
            provider_message_id="sim-004",
            thread_id="t-004",
            from_address="Plataforma de Vagas <noreply@vagas-plataforma.example>",
            subject="Atualização sobre sua candidatura",
            snippet="Sua candidatura avançou para a próxima etapa do processo seletivo.",
            received_at=now - timedelta(hours=10),
        ),
    ]
