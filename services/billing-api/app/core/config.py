"""Application settings, read from environment variables (12-factor style).

Nothing secret has a production default. In AWS the database password and
other secrets are injected by ECS from AWS Secrets Manager.
"""

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

LOCAL_ENVIRONMENTS = frozenset({"local", "test"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "billing-api"
    environment: str = "local"
    log_level: str = "INFO"

    # Database: either a full DATABASE_URL or the individual parts below.
    database_url: str | None = None
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "billing"
    db_user: str = "billing"
    db_password: str = "billing"
    # AWS: credentials are read from this Secrets Manager secret (RDS-managed, rotated) per new connection.
    db_secret_arn: str | None = None
    db_sslmode: str = "prefer"  # "require" in AWS (RDS PostgreSQL 16 enforces TLS)

    # Authentication
    auth_mode: Literal["local", "cognito"] = "local"
    # local mode: HS256 tokens signed with a development secret
    jwt_secret: str = "local-dev-secret-change-me-0123456789"
    jwt_issuer: str = "local-dev-issuer"
    # cognito mode: RS256 access tokens issued by an Amazon Cognito user pool
    cognito_region: str = "eu-central-1"
    cognito_user_pool_id: str = ""
    cognito_app_client_id: str = ""

    admin_group: str = "billing-admin"

    @model_validator(mode="after")
    def _no_dev_auth_outside_local(self) -> "Settings":
        # The local HS256 secret is public in the repository; it must never protect a deployed service.
        if self.auth_mode == "local" and self.environment not in LOCAL_ENVIRONMENTS:
            raise ValueError(f"AUTH_MODE=local is only allowed when ENVIRONMENT is one of {sorted(LOCAL_ENVIRONMENTS)}")
        return self

    @property
    def sqlalchemy_url(self) -> str | URL:
        if self.database_url:
            return self.database_url
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    @property
    def cognito_issuer(self) -> str:
        return f"https://cognito-idp.{self.cognito_region}.amazonaws.com/{self.cognito_user_pool_id}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
