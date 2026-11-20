"""Database credentials that survive automatic password rotation.

In AWS the RDS master password is generated and *rotated* by RDS (every 7 days by default) and kept
in AWS Secrets Manager. Injecting it once as an environment variable would break the service at the
first rotation, so the Billing API reads the secret when it opens a new connection instead:

* the secret is cached for a few minutes (no Secrets Manager call per connection);
* if PostgreSQL rejects the cached password, the cache is refreshed and the connection retried once.

Existing pooled connections are not affected by a password change in PostgreSQL, and pool_recycle
replaces them gradually with connections that use the current password.
"""

import json
import threading
import time
from collections.abc import Callable
from typing import Any


class SecretsManagerCredentials:
    def __init__(self, secret_id: str, ttl_seconds: float = 300.0, client_factory: Callable[[], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._secret_id = secret_id
        self._ttl = ttl_seconds
        self._client_factory = client_factory or self._default_client
        self._clock = clock
        self._lock = threading.Lock()
        self._cached: tuple[str, str] | None = None
        self._fetched_at = 0.0
        self._client = None

    @staticmethod
    def _default_client():
        import boto3  # imported lazily: only needed in AWS

        return boto3.client("secretsmanager")

    def get(self, refresh: bool = False) -> tuple[str, str]:
        with self._lock:
            expired = self._clock() - self._fetched_at > self._ttl
            if refresh or expired or self._cached is None:
                if self._client is None:
                    self._client = self._client_factory()
                secret = json.loads(self._client.get_secret_value(SecretId=self._secret_id)["SecretString"])
                self._cached = (secret["username"], secret["password"])
                self._fetched_at = self._clock()
            return self._cached


def is_auth_failure(exc: Exception) -> bool:
    return "password authentication failed" in str(exc).lower()
