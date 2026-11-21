"""Agent settings (environment variables). Secrets are injected by ECS from AWS Secrets Manager."""

import json
from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_ENVIRONMENTS = frozenset({"local", "test"})

# Local-development accounts only (AUTH_MODE=local). In AWS, users live in Amazon Cognito.
DEFAULT_LOCAL_USERS = {
    "5551000001": {"password": "Demo#2026!", "groups": ["subscriber"]},
    "5551000002": {"password": "Demo#2026!", "groups": ["subscriber"]},
    "ops-admin": {"password": "Admin#2026!", "groups": ["billing-admin"]},
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"
    log_level: str = "INFO"

    # Every REST call of the agent goes through the API Gateway, never directly to the Billing API.
    gateway_url: str = "http://localhost:8080"
    # The MCP path is separate from the gateway path (private network in AWS).
    mcp_url: str = "http://localhost:8001/mcp"
    mcp_token: str | None = None

    # LLM
    llm_provider: Literal["gemini"] = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    # Used when the primary model is overloaded or rate-limited (empty = no fallback).
    gemini_fallback_model: str = "gemini-3.5-flash-lite"
    llm_timeout_seconds: float = 10.0  # per LLM call; slower primary calls switch to the fallback model
    gemini_thinking_level: str = "low"  # Gemini 3 thinking depth: enough for tool selection, faster and cheaper
    # Whole chat turn (all LLM calls + tools); must stay below the API Gateway's 29 s integration timeout.
    chat_turn_budget_seconds: float = 25.0
    llm_max_output_tokens: int = 2048
    max_tool_rounds: int = 5

    # Authentication
    auth_mode: Literal["local", "cognito"] = "local"
    jwt_secret: str = "local-dev-secret-change-me-0123456789"
    jwt_issuer: str = "local-dev-issuer"
    local_users_json: str = json.dumps(DEFAULT_LOCAL_USERS)
    cognito_region: str = "eu-central-1"
    cognito_user_pool_id: str = ""
    cognito_app_client_id: str = ""
    subscriber_group: str = "subscriber"
    admin_group: str = "billing-admin"

    # Agent state
    payment_intent_ttl_seconds: int = 300
    conversation_ttl_seconds: int = 1800
    http_timeout_seconds: float = 15.0

    @model_validator(mode="after")
    def _no_dev_auth_outside_local(self) -> "Settings":
        # Local mode signs tokens with a secret and demo passwords that are public in the repository.
        if self.auth_mode == "local" and self.environment not in LOCAL_ENVIRONMENTS:
            raise ValueError(f"AUTH_MODE=local is only allowed when ENVIRONMENT is one of {sorted(LOCAL_ENVIRONMENTS)}")
        return self

    @property
    def local_users(self) -> dict[str, dict]:
        return json.loads(self.local_users_json)

    @property
    def cognito_issuer(self) -> str:
        return f"https://cognito-idp.{self.cognito_region}.amazonaws.com/{self.cognito_user_pool_id}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
