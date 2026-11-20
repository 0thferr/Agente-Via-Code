import os
from logging.config import fileConfig

from sqlalchemy import create_engine

import app.models  # noqa: F401  (registra todos os modelos no metadata)
from alembic import context
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    # Lê só DATABASE_URL (sem carregar todas as Settings), para que as migrations
    # não exijam segredos da aplicação como JWT_SECRET_KEY.
    url = config.attributes.get("database_url") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Defina a variável de ambiente DATABASE_URL.")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(get_database_url())
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
