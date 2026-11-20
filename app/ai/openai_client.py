"""Cliente da API da OpenAI (Chat Completions com saída estruturada) via HTTPX.

Usamos HTTPX diretamente porque ele já é dependência do projeto e a chamada
necessária é uma só; o SDK oficial seria uma dependência a mais sem ganho real.
"""

import logging

import httpx
from pydantic import SecretStr, ValidationError

from app.ai.llm import AIProviderError, OutputModel, strict_json_schema

logger = logging.getLogger(__name__)


class OpenAIStructuredLLM:
    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model_name = model
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
            transport=transport,
        )

    def generate(
        self, *, system_prompt: str, user_prompt: str, output_model: type[OutputModel]
    ) -> OutputModel:
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": output_model.__name__,
                    "strict": True,
                    "schema": strict_json_schema(output_model),
                },
            },
        }
        try:
            response = self._client.post("/chat/completions", json=payload)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise AIProviderError("O provedor de IA não respondeu a tempo.") from exc
        except httpx.HTTPStatusError as exc:
            # Registramos só o status: o corpo do erro pode ecoar dados enviados.
            logger.warning("openai_request_failed", extra={"status_code": exc.response.status_code})
            raise AIProviderError(
                f"O provedor de IA recusou a requisição (HTTP {exc.response.status_code})."
            ) from exc
        except httpx.HTTPError as exc:
            raise AIProviderError("Falha de comunicação com o provedor de IA.") from exc

        return _parse_output(response, output_model)


def _parse_output(response: httpx.Response, output_model: type[OutputModel]) -> OutputModel:
    try:
        message = response.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AIProviderError("Resposta do provedor de IA em formato inesperado.") from exc

    if message.get("refusal"):
        raise AIProviderError("O modelo de IA se recusou a responder.")
    try:
        return output_model.model_validate_json(message.get("content") or "")
    except ValidationError as exc:
        raise AIProviderError("A resposta da IA não segue o formato esperado.") from exc
