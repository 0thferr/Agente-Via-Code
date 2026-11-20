import json
import logging
import uuid

import pytest

from app.core.config import Settings, get_settings
from app.core.logging import JsonFormatter, redact
from app.core.security import (
    InvalidTokenError,
    TokenType,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.services.file_storage import LocalFileStorage


def test_password_hash_roundtrip():
    password_hash = hash_password("uma-senha-segura")

    assert password_hash != "uma-senha-segura"
    assert verify_password("uma-senha-segura", password_hash)
    assert not verify_password("outra-senha", password_hash)


def test_verify_password_with_malformed_hash_returns_false():
    assert not verify_password("qualquer", "not-a-hash")


def test_token_roundtrip_and_type_check():
    settings = get_settings()
    user_id = uuid.uuid4()
    token, _ = create_token(user_id, TokenType.ACCESS, settings)

    assert decode_token(token, TokenType.ACCESS, settings).user_id == user_id
    with pytest.raises(InvalidTokenError):
        decode_token(token, TokenType.REFRESH, settings)


def test_token_signed_with_other_key_is_rejected():
    settings = get_settings()
    other = settings.model_copy(update={"jwt_secret_key": "x" * 40})
    token, _ = create_token(uuid.uuid4(), TokenType.ACCESS, other)

    with pytest.raises(InvalidTokenError):
        decode_token(token, TokenType.ACCESS, settings)


def test_settings_reject_example_secret():
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        Settings(jwt_secret_key="change-me-" + "x" * 32)


def test_redact_masks_sensitive_keys_recursively():
    data = {"password": "a", "nested": {"access_token": "b", "ok": 1}, "items": [{"secret": "c"}]}

    assert redact(data) == {
        "password": "***",
        "nested": {"access_token": "***", "ok": 1},
        "items": [{"secret": "***"}],
    }


def test_json_formatter_redacts_extra_fields():
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "msg", None, None)
    record.authorization = "Bearer abc"

    output = json.loads(JsonFormatter().format(record))

    assert output["authorization"] == "***"


@pytest.mark.parametrize("key", ["../etc/passwd", "abc.pdf", "/abs/path.pdf"])
def test_storage_rejects_unsafe_keys(tmp_path, key):
    with pytest.raises(ValueError):
        LocalFileStorage(tmp_path).path_for(key)
