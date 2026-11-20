from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, text

from app.core.config import get_settings
from app.db.orm_models import Base
from app.db.session import build_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Only one migrator at a time, even if several containers start together.
MIGRATION_LOCK_ID = 4458_0002


def run_migrations_offline() -> None:
    context.configure(url=str(get_settings().sqlalchemy_url), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Same connection logic as the application (incl. Secrets Manager credentials in AWS);
    # tests may pass an explicit URL instead.
    url = config.attributes.get("database_url")
    engine = create_engine(url) if url else build_engine(get_settings())
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:id)"), {"id": MIGRATION_LOCK_ID})
        connection.commit()
        try:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": MIGRATION_LOCK_ID})
            connection.commit()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
