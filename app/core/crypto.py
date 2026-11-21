"""Criptografia simétrica para segredos guardados no banco (tokens OAuth etc.).

Usa Fernet (AES + HMAC, da biblioteca `cryptography`): o valor cifrado também é
autenticado, então uma adulteração no banco é detectada na leitura.
"""

from cryptography.fernet import Fernet, InvalidToken

from app.core.errors import DomainError


class EncryptionNotConfiguredError(DomainError):
    code = "encryption_not_configured"


class SecretCipher:
    def __init__(self, key: str | None) -> None:
        self._fernet = Fernet(key.encode()) if key else None

    @property
    def is_configured(self) -> bool:
        return self._fernet is not None

    def encrypt(self, value: str) -> str:
        return self._require().encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._require().decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise DomainError(
                "Segredo armazenado inválido ou chave de criptografia trocada."
            ) from exc

    def _require(self) -> Fernet:
        if self._fernet is None:
            raise EncryptionNotConfiguredError(
                "Defina TOKEN_ENCRYPTION_KEY para guardar credenciais de integrações."
            )
        return self._fernet
