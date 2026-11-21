"""Armazenamento de arquivos privados em disco local.

Os arquivos são salvos com nomes aleatórios (nunca com o nome enviado pelo
usuário), o que evita path traversal e colisões. Para produção, esta classe pode
ser trocada por uma implementação com S3 ou similar mantendo a mesma interface.
"""

import re
import uuid
from pathlib import Path

STORAGE_KEY_PATTERN = re.compile(r"^[0-9a-f]{32}\.[a-z0-9]{1,8}$")


class LocalFileStorage:
    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir.resolve()

    def save(self, content: bytes, extension: str) -> str:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        key = f"{uuid.uuid4().hex}.{extension}"
        self._path_for(key).write_bytes(content)
        return key

    def path_for(self, key: str) -> Path:
        path = self._path_for(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path

    def delete(self, key: str) -> None:
        self._path_for(key).unlink(missing_ok=True)

    def _path_for(self, key: str) -> Path:
        if not STORAGE_KEY_PATTERN.fullmatch(key):
            raise ValueError("Chave de armazenamento inválida.")
        return self._base_dir / key
