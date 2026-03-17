import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import DATABASE_URL as DEFAULT_DATABASE_URL
from models import db

config = context.config
fileConfig(config.config_file_name)

if not config.get_main_option("sqlalchemy.url"):
    env_url = os.environ.get("SQLALCHEMY_DATABASE_URI") or os.environ.get(
        "DATABASE_URL"
    )
    if env_url:
        config.set_main_option("sqlalchemy.url", env_url)
    else:
        config.set_main_option(
            "sqlalchemy.url",
            "postgresql://guaardvark:guaardvark@localhost:5432/guaardvark",
        )

# Ensure the database URL is provided for Alembic when invoked via Flask.
# Prefer an explicit DATABASE_URL environment variable, falling back to
# the project's configured default.
database_url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = db.metadata


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # render_as_batch=True kept for migration compatibility
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
