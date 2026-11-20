from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.automation.schedule import next_run_at
from app.schemas.automation import AutomationRuleCreate

# 2026-11-20 é uma sexta-feira (weekday 4).
FRIDAY_NOON_UTC = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def test_fixed_time_later_today_in_rule_timezone():
    # 12:00 UTC = 09:00 em São Paulo (UTC-3); 10:00 local ainda não passou.
    result = next_run_at(
        after=FRIDAY_NOON_UTC,
        timezone="America/Sao_Paulo",
        days_of_week=[],
        time_of_day="10:00",
        interval_minutes=None,
    )

    assert result == datetime(2026, 11, 20, 13, 0, tzinfo=UTC)


def test_fixed_time_skips_to_next_allowed_weekday():
    # Só segundas (0): a próxima é 23/11 às 08:00 locais = 11:00 UTC.
    result = next_run_at(
        after=FRIDAY_NOON_UTC,
        timezone="America/Sao_Paulo",
        days_of_week=[0],
        time_of_day="08:00",
        interval_minutes=None,
    )

    assert result == datetime(2026, 11, 23, 11, 0, tzinfo=UTC)


def test_time_already_passed_today_goes_to_tomorrow():
    result = next_run_at(
        after=FRIDAY_NOON_UTC,
        timezone="UTC",
        days_of_week=[],
        time_of_day="11:59",
        interval_minutes=None,
    )

    assert result == datetime(2026, 11, 21, 11, 59, tzinfo=UTC)


def test_interval_schedule():
    result = next_run_at(
        after=FRIDAY_NOON_UTC,
        timezone="UTC",
        days_of_week=[],
        time_of_day=None,
        interval_minutes=90,
    )

    assert result == FRIDAY_NOON_UTC + timedelta(minutes=90)


@pytest.mark.parametrize(
    "overrides",
    [
        {"time_of_day": "08:00", "interval_minutes": 60},  # os dois tipos
        {"time_of_day": None, "interval_minutes": None},  # nenhum
        {"time_of_day": "25:00"},
        {"timezone": "Mars/Olympus"},
        {"time_of_day": None, "interval_minutes": 5},  # abaixo do mínimo
        {"days_of_week": [7]},
        {"config": {"max_items": 1000}},
    ],
)
def test_invalid_rules(overrides):
    data = {
        "name": "Analisar",
        "task_type": "analyze_jobs",
        "time_of_day": "08:00",
        **overrides,
    }

    with pytest.raises(ValidationError):
        AutomationRuleCreate.model_validate(data)


def test_config_is_validated_and_normalized_per_task():
    rule = AutomationRuleCreate(
        name="Buscar", task_type="discover_jobs", interval_minutes=60, config={}
    )

    assert rule.config["filters"]["keywords"] == []
