"""Fuso horário do usuário: validação e cálculo do "dia" local.

Limites diários e datas de e-mails dependem de quando o dia começa para a
pessoa (ex.: meia-noite em Brasília), não da meia-noite UTC.
"""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "America/Sao_Paulo"


def validate_timezone(value: str) -> str:
    """Aceita só nomes IANA válidos (ex.: America/Sao_Paulo)."""
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Fuso horário IANA inválido (ex.: America/Sao_Paulo).") from exc
    return value


def local_start_of_day(timezone: str, now: datetime | None = None) -> datetime:
    """Meia-noite de hoje no fuso informado, convertida para UTC."""
    zone = ZoneInfo(timezone)
    local_today = (now or datetime.now(UTC)).astimezone(zone).date()
    return datetime.combine(local_today, time.min, tzinfo=zone).astimezone(UTC)


def local_date_iso(timezone: str, now: datetime | None = None) -> str:
    """Data de hoje (AAAA-MM-DD) no fuso informado."""
    return (now or datetime.now(UTC)).astimezone(ZoneInfo(timezone)).date().isoformat()
