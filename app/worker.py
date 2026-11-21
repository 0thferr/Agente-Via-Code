"""Processo do worker de automações (separado da API).

Uso: python -m app.worker

O APScheduler dispara um "ciclo" a cada WORKER_TICK_SECONDS. Cada ciclo recupera
envios interrompidos, enfileira as regras vencidas e executa alguns jobs. A
coordenação entre várias instâncias é feita no banco (ver app/automation/runner.py),
então rodar mais de um worker é seguro.
"""

import logging
import os
import socket
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler

from app.automation.runner import run_tick
from app.automation.tasks import TaskContext
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_session_factory
from app.wiring import (
    get_channel_registry,
    get_cipher,
    get_cover_letter_writer,
    get_email_clients,
    get_provider_registry,
    get_requirement_analyzer,
    get_submitter_registry,
)

logger = logging.getLogger("app.worker")


def build_task_context() -> TaskContext:
    return TaskContext(
        providers=get_provider_registry(),
        analyzer=get_requirement_analyzer(),
        writer=get_cover_letter_writer(),
        submitters=get_submitter_registry(),
        email_clients=get_email_clients(),
        cipher=get_cipher(),
        channels=get_channel_registry(),
        email_lookback=timedelta(days=get_settings().email_sync_lookback_days),
    )


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    # O APScheduler registra cada ciclo em INFO; só nos interessam avisos e erros dele.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    context = build_task_context()
    session_factory = get_session_factory()

    def tick() -> None:
        result = run_tick(
            session_factory,
            context,
            worker_id=worker_id,
            now=datetime.now(UTC),
            lease=timedelta(seconds=settings.worker_lease_seconds),
            stale_submission_after=timedelta(minutes=settings.stale_submission_minutes),
            max_jobs=settings.worker_max_jobs_per_tick,
        )
        if any(vars(result).values()):
            logger.info("worker_tick", extra=vars(result))

    scheduler = BlockingScheduler(timezone=UTC)
    # max_instances=1 + coalesce: um ciclo nunca se sobrepõe ao anterior.
    scheduler.add_job(
        tick,
        "interval",
        seconds=settings.worker_tick_seconds,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC),
    )
    logger.info("worker_started", extra={"worker_id": worker_id})
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("worker_stopped", extra={"worker_id": worker_id})


if __name__ == "__main__":
    main()
