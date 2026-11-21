"""Fuso horário do usuário: início do dia local e datas lidas dos e-mails."""

from datetime import UTC, datetime

import pytest

from app.core.timezones import DEFAULT_TIMEZONE, local_start_of_day, validate_timezone
from app.services.communication.classifier import classify, extract_dates

SAO_PAULO = "America/Sao_Paulo"


def test_default_timezone_is_brasilia():
    assert DEFAULT_TIMEZONE == SAO_PAULO


def test_start_of_day_uses_local_midnight():
    # 02:00 UTC do dia 21 ainda é 23:00 do dia 20 em São Paulo (UTC-3).
    now = datetime(2026, 11, 21, 2, 0, tzinfo=UTC)

    assert local_start_of_day(SAO_PAULO, now) == datetime(2026, 11, 20, 3, 0, tzinfo=UTC)


def test_start_of_day_after_local_midnight():
    now = datetime(2026, 11, 21, 4, 0, tzinfo=UTC)  # 01:00 do dia 21 em São Paulo

    assert local_start_of_day(SAO_PAULO, now) == datetime(2026, 11, 21, 3, 0, tzinfo=UTC)


@pytest.mark.parametrize("value", ["Mars/Olympus", "", "UTC+3"])
def test_invalid_timezones_are_rejected(value):
    with pytest.raises(ValueError):
        validate_timezone(value)


def test_email_dates_are_interpreted_in_user_timezone():
    reference = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

    dates = extract_dates("Entrevista dia 22/11 às 18h", reference, SAO_PAULO)

    # 18h em São Paulo = 21h UTC.
    assert dates == [datetime(2026, 11, 22, 21, 0, tzinfo=UTC)]


def test_deadline_without_time_ends_at_local_end_of_day():
    reference = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

    result = classify("Teste técnico", "Entregar até 22/11", reference, SAO_PAULO)

    # 23:59 de 22/11 em São Paulo = 02:59 UTC de 23/11.
    assert result.deadline_at == datetime(2026, 11, 23, 2, 59, tzinfo=UTC)
