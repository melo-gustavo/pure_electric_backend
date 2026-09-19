import asyncio
import re
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from alembic.script import base as script_base

from databases.postgres import DATABASE_URL
from models.base_model import Base
from migrations.import_model import *

config = context.config
config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _next_migration_number() -> int:
    """Return the next sequential prefix for a migration file."""
    script_location = config.get_main_option("script_location") or "migrations"
    versions_dir = Path(script_location, "versions")
    numbers = []
    for file in versions_dir.glob("*.py"):
        match = re.match(r"(\d{4})_", file.name)
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


_original_rev_path = script_base.ScriptDirectory._rev_path


def _numbered_rev_path(self, path, rev_id, message, create_date):
    """Compute the migration file path with a sequential numeric prefix."""
    self.file_template = f"{_next_migration_number():04d}_%(slug)s"
    return _original_rev_path(self, path, rev_id, message, create_date)


script_base.ScriptDirectory._rev_path = _numbered_rev_path


def run_migrations_offline() -> None:
    """Run migrations in offline mode without a live database."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    """Run migrations against the given sync connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    """Run migrations online through the async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in online mode against the live database."""
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()