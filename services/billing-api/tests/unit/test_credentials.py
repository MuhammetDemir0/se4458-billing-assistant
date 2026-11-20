"""Rotating database credentials (Secrets Manager), tested with a fake client."""

import json

import pytest

from app.db.credentials import SecretsManagerCredentials, is_auth_failure


class FakeSecretsManager:
    def __init__(self):
        self.password = "first"
        self.calls = 0

    def get_secret_value(self, SecretId):  # noqa: N803 - boto3 argument name
        self.calls += 1
        return {"SecretString": json.dumps({"username": "billing_admin", "password": self.password})}


@pytest.fixture
def fake():
    return FakeSecretsManager()


def test_secret_is_cached_within_ttl(fake):
    now = [0.0]
    creds = SecretsManagerCredentials("arn:secret", ttl_seconds=300, client_factory=lambda: fake, clock=lambda: now[0])
    assert creds.get() == ("billing_admin", "first")
    now[0] = 100
    assert creds.get() == ("billing_admin", "first")
    assert fake.calls == 1


def test_secret_is_reloaded_after_ttl(fake):
    now = [0.0]
    creds = SecretsManagerCredentials("arn:secret", ttl_seconds=300, client_factory=lambda: fake, clock=lambda: now[0])
    creds.get()
    fake.password = "rotated"
    now[0] = 301
    assert creds.get() == ("billing_admin", "rotated")


def test_refresh_after_rejected_password(fake):
    creds = SecretsManagerCredentials("arn:secret", client_factory=lambda: fake)
    creds.get()
    fake.password = "rotated"
    assert creds.get() == ("billing_admin", "first")              # still cached
    assert creds.get(refresh=True) == ("billing_admin", "rotated")  # what the connect hook does on auth failure


def test_auth_failure_detection():
    assert is_auth_failure(Exception('FATAL:  password authentication failed for user "billing_admin"'))
    assert not is_auth_failure(Exception("connection timeout expired"))


def test_local_auth_is_refused_outside_local_environments():
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="AUTH_MODE=local"):
        Settings(environment="aws", auth_mode="local")
    assert Settings(environment="aws", auth_mode="cognito").auth_mode == "cognito"
