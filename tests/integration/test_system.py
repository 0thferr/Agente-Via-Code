from redis.exceptions import ConnectionError as RedisConnectionError

from app.api import deps
from app.main import app


class FakeRedis:
    def __init__(self, healthy: bool) -> None:
        self.healthy = healthy

    def ping(self) -> bool:
        if not self.healthy:
            raise RedisConnectionError("down")
        return True


def test_health(client):
    assert client.get("/api/v1/health").json() == {"status": "ok"}


def test_ready_when_dependencies_are_up(client):
    app.dependency_overrides[deps.get_redis_client] = lambda: FakeRedis(healthy=True)

    response = client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json()["checks"] == {"database": "ok", "redis": "ok"}


def test_not_ready_when_redis_is_down(client):
    app.dependency_overrides[deps.get_redis_client] = lambda: FakeRedis(healthy=False)

    response = client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "unavailable"


def test_responses_carry_request_id(client):
    response = client.get("/api/v1/health", headers={"X-Request-ID": "abc123"})

    assert response.headers["X-Request-ID"] == "abc123"
