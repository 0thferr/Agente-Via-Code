"""Clientes Gmail (Google) e Outlook (Microsoft Graph) com OAuth 2.0 + PKCE.

Escopos mínimos e só de leitura: o sistema não tem permissão para enviar e-mails,
então nunca responde recrutadores por conta própria. Só metadados e o trecho
curto (snippet) são lidos; o corpo das mensagens não é baixado.

Atenção: estes clientes foram testados apenas com respostas HTTP simuladas.
Valide com credenciais reais (app registrado no Google Cloud / Microsoft Entra).
"""

import email.utils
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from app.integrations.email.base import EmailClient, EmailMessage, EmailProviderError, OAuthTokens
from app.models.enums import EmailProvider

MAX_SNIPPET_LENGTH = 300


class _OAuthEmailClient(EmailClient):
    authorize_endpoint: str
    token_endpoint: str
    scopes: list[str]
    extra_authorize_params: dict[str, str] = {}

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        timeout_seconds: float = 15,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.Client(timeout=timeout_seconds, transport=transport)

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            **self.extra_authorize_params,
        }
        return f"{self.authorize_endpoint}?{urlencode(params)}"

    def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> OAuthTokens:
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
            }
        )

    def refresh(self, refresh_token: str) -> OAuthTokens:
        tokens = self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )
        # Alguns provedores não devolvem um novo refresh token: mantemos o atual.
        return OAuthTokens(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token or refresh_token,
            expires_at=tokens.expires_at,
            scopes=tokens.scopes,
        )

    def _token_request(self, data: dict[str, str]) -> OAuthTokens:
        payload = {**data, "client_id": self._client_id, "client_secret": self._client_secret}
        body = self._request("POST", self.token_endpoint, data=payload)
        try:
            expires_in = int(body.get("expires_in", 0))
            return OAuthTokens(
                access_token=body["access_token"],
                refresh_token=body.get("refresh_token"),
                expires_at=datetime.now(UTC) + timedelta(seconds=expires_in)
                if expires_in
                else None,
                scopes=str(body.get("scope", "")).split(),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise EmailProviderError("Resposta de token em formato inesperado.") from exc

    def _request(self, method: str, url: str, **kwargs) -> dict:
        try:
            response = self._http.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            # Nunca incluímos o corpo da resposta: pode ecoar tokens ou códigos.
            raise EmailProviderError(
                f"{self.provider} recusou a requisição (HTTP {exc.response.status_code})."
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise EmailProviderError(f"Falha de comunicação com {self.provider}.") from exc

    @staticmethod
    def _auth(access_token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {access_token}"}


class GmailClient(_OAuthEmailClient):
    provider = EmailProvider.GMAIL
    authorize_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
    token_endpoint = "https://oauth2.googleapis.com/token"  # noqa: S105 (URL, não segredo)
    api_base = "https://gmail.googleapis.com/gmail/v1/users/me"
    scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
    # offline + consent: necessário para o Google devolver refresh token.
    extra_authorize_params = {"access_type": "offline", "prompt": "consent"}

    def account_email(self, access_token: str) -> str:
        body = self._request("GET", f"{self.api_base}/profile", headers=self._auth(access_token))
        return body["emailAddress"]

    def list_messages(self, access_token: str, since: datetime, limit: int) -> list[EmailMessage]:
        headers = self._auth(access_token)
        listing = self._request(
            "GET",
            f"{self.api_base}/messages",
            headers=headers,
            params={"q": f"after:{int(since.timestamp())}", "maxResults": limit},
        )
        messages = []
        for item in listing.get("messages", []):
            detail = self._request(
                "GET",
                f"{self.api_base}/messages/{item['id']}",
                headers=headers,
                params=[
                    ("format", "metadata"),
                    ("metadataHeaders", "From"),
                    ("metadataHeaders", "Subject"),
                ],
            )
            header_values = {
                header["name"].lower(): header["value"]
                for header in detail.get("payload", {}).get("headers", [])
            }
            messages.append(
                EmailMessage(
                    provider_message_id=detail["id"],
                    thread_id=detail.get("threadId"),
                    from_address=header_values.get("from", ""),
                    subject=header_values.get("subject", ""),
                    snippet=detail.get("snippet", "")[:MAX_SNIPPET_LENGTH],
                    received_at=datetime.fromtimestamp(int(detail["internalDate"]) / 1000, UTC),
                )
            )
        return messages


class OutlookClient(_OAuthEmailClient):
    provider = EmailProvider.OUTLOOK
    authorize_endpoint = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    token_endpoint = "https://login.microsoftonline.com/common/oauth2/v2.0/token"  # noqa: S105
    api_base = "https://graph.microsoft.com/v1.0/me"
    scopes = ["offline_access", "User.Read", "Mail.Read"]

    def account_email(self, access_token: str) -> str:
        body = self._request("GET", self.api_base, headers=self._auth(access_token))
        return body.get("mail") or body["userPrincipalName"]

    def list_messages(self, access_token: str, since: datetime, limit: int) -> list[EmailMessage]:
        since_iso = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = self._request(
            "GET",
            f"{self.api_base}/messages",
            headers=self._auth(access_token),
            params={
                "$filter": f"receivedDateTime ge {since_iso}",
                "$select": "id,conversationId,subject,from,receivedDateTime,bodyPreview",
                "$orderby": "receivedDateTime desc",
                "$top": limit,
            },
        )
        return [
            EmailMessage(
                provider_message_id=item["id"],
                thread_id=item.get("conversationId"),
                from_address=_format_sender(item.get("from", {}).get("emailAddress", {})),
                subject=item.get("subject") or "",
                snippet=(item.get("bodyPreview") or "")[:MAX_SNIPPET_LENGTH],
                received_at=datetime.fromisoformat(item["receivedDateTime"].replace("Z", "+00:00")),
            )
            for item in body.get("value", [])
        ]


def _format_sender(sender: dict) -> str:
    return email.utils.formataddr((sender.get("name", ""), sender.get("address", "")))
