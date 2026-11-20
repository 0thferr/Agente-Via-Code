"""Cálculo da próxima execução de uma regra de automação.

Execuções perdidas (ex.: worker parado por horas) não são repetidas uma a uma:
a próxima execução é sempre calculada a partir de "agora". Isso evita uma rajada
de tarefas acumuladas quando o sistema volta.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DAYS_IN_WEEK = 7


def next_run_at(
    *,
    after: datetime,
    timezone: str,
    days_of_week: list[int],
    time_of_day: str | None,
    interval_minutes: int | None,
) -> datetime:
    """Próximo instante (em UTC/aware) estritamente posterior a `after`.

    `days_of_week` usa 0 = segunda ... 6 = domingo (vazio = todos os dias).
    """
    if interval_minutes is not None:
        return after + timedelta(minutes=interval_minutes)
    if time_of_day is None:
        raise ValueError("Informe time_of_day ou interval_minutes.")

    zone = ZoneInfo(timezone)
    hour, minute = (int(part) for part in time_of_day.split(":"))
    allowed_days = set(days_of_week) or set(range(DAYS_IN_WEEK))
    local_now = after.astimezone(zone)
    # Até 8 dias à frente cobre qualquer combinação de dias da semana.
    for offset in range(DAYS_IN_WEEK + 1):
        day: date = local_now.date() + timedelta(days=offset)
        if day.weekday() not in allowed_days:
            continue
        candidate = datetime.combine(day, time(hour, minute), tzinfo=zone)
        if candidate > after:
            return candidate
    raise ValueError("Não foi possível calcular a próxima execução.")
