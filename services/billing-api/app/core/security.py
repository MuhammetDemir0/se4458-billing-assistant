"""JWT verification and the authenticated principal.

The API Gateway already rejects requests without a valid token on protected
routes. The service verifies the token again (defense in depth) so that it is
never safe to bypass the gateway, and so that ownership rules ("a subscriber
may only see their own bill") are enforced next to the business logic.

Both verifiers read the same claim shape as an Amazon Cognito *access token*:
  username        -> subscriber number (or an operator name for admins)
  cognito:groups  -> roles, e.g. ["subscriber"] or ["billing-admin"]
  token_use       -> must be "access"
"""

from dataclasses import dataclass
from typing import Protocol

import jwt

from app.core.config import Settings
from app.core.errors import UnauthorizedError


@dataclass(frozen=True)
class Principal:
    username: str
    groups: frozenset[str]
    admin_group: str = "billing-admin"

    @property
    def is_admin(self) -> bool:
        return self.admin_group in self.groups

    def can_access_subscriber(self, subscriber_no: str) -> bool:
        return self.is_admin or self.username == subscriber_no


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Principal: ...


def _principal_from_claims(claims: dict, admin_group: str) -> Principal:
    if claims.get("token_use") != "access":
        raise UnauthorizedError("An access token is required.", code="INVALID_TOKEN")
    username = claims.get("username")
    if not username:
        raise UnauthorizedError("Token has no username claim.", code="INVALID_TOKEN")
    groups = frozenset(claims.get("cognito:groups") or [])
    return Principal(username=str(username), groups=groups, admin_group=admin_group)


class LocalTokenVerifier:
    """HS256 tokens for local development and automated tests."""

    def __init__(self, secret: str, issuer: str, admin_group: str) -> None:
        self._secret = secret
        self._issuer = issuer
        self._admin_group = admin_group

    def verify(self, token: str) -> Principal:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                issuer=self._issuer,
                options={"require": ["exp", "iss", "username"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise UnauthorizedError("Token has expired.", code="TOKEN_EXPIRED") from exc
        except jwt.PyJWTError as exc:
            raise UnauthorizedError("Token is invalid.", code="INVALID_TOKEN") from exc
        return _principal_from_claims(claims, self._admin_group)


class CognitoTokenVerifier:
    """RS256 access tokens from an Amazon Cognito user pool (keys from its JWKS endpoint)."""

    def __init__(self, issuer: str, app_client_id: str, admin_group: str) -> None:
        self._issuer = issuer
        self._client_id = app_client_id
        self._admin_group = admin_group
        self._jwks = jwt.PyJWKClient(f"{issuer}/.well-known/jwks.json", cache_keys=True, lifespan=3600)

    def verify(self, token: str) -> Principal:
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=self._issuer,
                options={"require": ["exp", "iss", "client_id"], "verify_aud": False},
            )
        except jwt.ExpiredSignatureError as exc:
            raise UnauthorizedError("Token has expired.", code="TOKEN_EXPIRED") from exc
        except jwt.PyJWTError as exc:
            raise UnauthorizedError("Token is invalid.", code="INVALID_TOKEN") from exc
        # Cognito access tokens carry the app client in "client_id" instead of "aud".
        if claims.get("client_id") != self._client_id:
            raise UnauthorizedError("Token was issued for another client.", code="INVALID_TOKEN")
        return _principal_from_claims(claims, self._admin_group)


def build_token_verifier(settings: Settings) -> TokenVerifier:
    if settings.auth_mode == "cognito":
        return CognitoTokenVerifier(settings.cognito_issuer, settings.cognito_app_client_id, settings.admin_group)
    return LocalTokenVerifier(settings.jwt_secret, settings.jwt_issuer, settings.admin_group)
