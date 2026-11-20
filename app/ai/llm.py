"""Interface mínima para modelos de linguagem com saída estruturada.

Os serviços pedem "um objeto do tipo X" e recebem um modelo Pydantic já
validado. Trocar de provedor (OpenAI, outro) significa só implementar esta
interface.
"""

import copy
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

OutputModel = TypeVar("OutputModel", bound=BaseModel)


class AIProviderError(Exception):
    """Falha ao obter uma resposta válida do provedor de IA."""


class StructuredLLM(Protocol):
    provider_name: str
    model_name: str

    def generate(
        self, *, system_prompt: str, user_prompt: str, output_model: type[OutputModel]
    ) -> OutputModel:
        """Gera uma resposta que obedece ao schema de `output_model`."""


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Adapta o JSON Schema do Pydantic ao modo estrito de saída estruturada.

    No modo estrito todo objeto precisa declarar `additionalProperties: false` e
    listar todas as propriedades como obrigatórias. Por isso os modelos de saída
    da IA não têm valores padrão: campos opcionais usam `T | None`.
    """
    schema = copy.deepcopy(model.model_json_schema())
    _make_strict(schema)
    return schema


def _make_strict(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"])
        for value in node.values():
            _make_strict(value)
    elif isinstance(node, list):
        for item in node:
            _make_strict(item)
