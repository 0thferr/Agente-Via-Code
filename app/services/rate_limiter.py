"""Rate limiting por janela fixa.

A implementação com Redis funciona com várias instâncias da API; a versão em
memória existe para testes e para uso local sem Redis.
"""

import threading
import time
from typing import Protocol

from redis import Redis
from redis.exceptions import RedisError

from app.core.errors import RateLimitExceededError


class RateLimiter(Protocol):
    def hit(self, key: str, limit: int, window_seconds: int) -> None:
        """Conta uma tentativa e lança RateLimitExceededError se passar do limite."""


class RedisRateLimiter:
    def __init__(self, client: Redis) -> None:
        self._client = client

    def hit(self, key: str, limit: int, window_seconds: int) -> None:
        redis_key = f"rate_limit:{key}"
        try:
            # SET ... EX ... NX cria o contador com prazo só na primeira tentativa da
            # janela; INCR preserva o prazo. Funciona em qualquer versão do Redis
            # (EXPIRE ... NX exigiria Redis 7+).
            pipeline = self._client.pipeline()
            pipeline.set(redis_key, 0, ex=window_seconds, nx=True)
            pipeline.incr(redis_key)
            _, attempts = pipeline.execute()
        except RedisError as exc:
            # Falha fechada: sem Redis não conseguimos proteger o login, então recusamos.
            raise RateLimitExceededError(
                "Serviço de proteção indisponível. Tente novamente em instantes."
            ) from exc
        if attempts > limit:
            raise RateLimitExceededError("Muitas tentativas. Aguarde e tente novamente.")


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._attempts: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        with self._lock:
            count, window_start = self._attempts.get(key, (0, now))
            if now - window_start >= window_seconds:
                count, window_start = 0, now
            count += 1
            self._attempts[key] = (count, window_start)
        if count > limit:
            raise RateLimitExceededError("Muitas tentativas. Aguarde e tente novamente.")
