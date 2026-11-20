from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.credentials import SecretsManagerCredentials, is_auth_failure


def build_engine(settings: Settings, credentials: SecretsManagerCredentials | None = None) -> Engine:
    engine = create_engine(
        settings.sqlalchemy_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_recycle=1800,
        connect_args={"sslmode": settings.db_sslmode, "connect_timeout": 10},
    )
    if credentials is None and settings.db_secret_arn:
        credentials = SecretsManagerCredentials(settings.db_secret_arn)
    if credentials is not None:
        attach_rotating_credentials(engine, credentials)
    return engine


def attach_rotating_credentials(engine: Engine, credentials: SecretsManagerCredentials) -> None:
    """Fill user/password from Secrets Manager for every new connection (see app/db/credentials.py)."""

    @event.listens_for(engine, "do_connect")
    def _connect(dialect, conn_rec, cargs, cparams):
        cparams["user"], cparams["password"] = credentials.get()
        try:
            return dialect.connect(*cargs, **cparams)
        except Exception as exc:
            if not is_auth_failure(exc):
                raise
            cparams["user"], cparams["password"] = credentials.get(refresh=True)  # password was rotated
            return dialect.connect(*cargs, **cparams)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def ping(engine: Engine) -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - health check reports any failure as "down"
        return False
