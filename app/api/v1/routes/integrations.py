import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import CurrentUserDep, ProviderRegistryDep, SessionDep, SettingsDep
from app.core.config import Settings
from app.core.crypto import SecretCipher
from app.core.errors import ValidationError
from app.integrations.email.base import EmailClient
from app.integrations.notifications.channels import ChannelRegistry
from app.models.enums import EmailProvider
from app.schemas.communication import (
    ConnectResponse,
    EmailAccountOut,
    EmailIntegrationStatus,
    IntegrationsOverview,
    JobProviderStatus,
)
from app.services.communication import email_service
from app.wiring import get_channel_registry, get_cipher, get_email_clients

router = APIRouter(prefix="/integrations", tags=["integrations"])

EmailClientsDep = Annotated[dict[EmailProvider, EmailClient], Depends(get_email_clients)]
CipherDep = Annotated[SecretCipher, Depends(get_cipher)]
ChannelsDep = Annotated[ChannelRegistry, Depends(get_channel_registry)]


def _redirect_uri(settings: Settings, provider: EmailProvider) -> str:
    return f"{settings.oauth_redirect_base_url}/api/v1/integrations/{provider.value}/callback"


def _client(clients: dict[EmailProvider, EmailClient], provider: EmailProvider) -> EmailClient:
    client = clients.get(provider)
    if client is None:
        raise ValidationError(
            f"{provider} não está configurado no servidor (credenciais OAuth ausentes)."
        )
    return client


@router.get("", response_model=IntegrationsOverview)
def list_integrations(
    session: SessionDep,
    registry: ProviderRegistryDep,
    clients: EmailClientsDep,
    channels: ChannelsDep,
    current_user: CurrentUserDep,
) -> IntegrationsOverview:
    """Estado real de cada integração: o que funciona, o que é simulado e o que é manual."""
    accounts = email_service.list_accounts(session, current_user.id)
    return IntegrationsOverview(
        job_providers=[
            JobProviderStatus(
                key=provider.info.key,
                name=provider.info.name,
                integration_status=provider.info.integration_status,
            )
            for provider in registry.all()
        ],
        email=[
            EmailIntegrationStatus(
                provider=provider,
                configured=provider in clients,
                accounts=[
                    EmailAccountOut.model_validate(account)
                    for account in accounts
                    if account.provider is provider
                ],
            )
            for provider in EmailProvider
        ],
        notification_channels=channels.available(),
    )


@router.post("/{provider}/connect", response_model=ConnectResponse)
def connect(
    provider: EmailProvider,
    session: SessionDep,
    settings: SettingsDep,
    clients: EmailClientsDep,
    cipher: CipherDep,
    current_user: CurrentUserDep,
) -> ConnectResponse:
    """Inicia a conexão. Para Gmail/Outlook devolve a URL de autorização (OAuth + PKCE)."""
    client = _client(clients, provider)
    if not client.uses_oauth:
        account = email_service.connect_simulated(session, client, cipher, current_user.id)
        return ConnectResponse(account=EmailAccountOut.model_validate(account))
    url = email_service.start_connection(
        session,
        client,
        cipher,
        current_user.id,
        _redirect_uri(settings, provider),
        datetime.now(UTC),
    )
    return ConnectResponse(authorization_url=url)


@router.get("/{provider}/callback", response_model=EmailAccountOut)
def oauth_callback(
    provider: EmailProvider,
    session: SessionDep,
    settings: SettingsDep,
    clients: EmailClientsDep,
    cipher: CipherDep,
    state: Annotated[str, Query(min_length=16, max_length=200)],
    code: Annotated[str, Query(min_length=1, max_length=2000)],
) -> EmailAccountOut:
    """Retorno do provedor OAuth. Sem token de acesso: o `state` identifica o usuário
    e só vale uma vez, por até 10 minutos."""
    account = email_service.complete_connection(
        session,
        _client(clients, provider),
        cipher,
        state=state,
        code=code,
        redirect_uri=_redirect_uri(settings, provider),
        now=datetime.now(UTC),
    )
    return EmailAccountOut.model_validate(account)


@router.delete("/{provider}/disconnect", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(
    provider: EmailProvider,
    session: SessionDep,
    current_user: CurrentUserDep,
    account_id: uuid.UUID | None = None,
) -> Response:
    """Remove a conta, seus tokens e as mensagens importadas dela."""
    email_service.disconnect(session, current_user.id, provider, account_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
