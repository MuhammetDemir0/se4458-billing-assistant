"""Sign-in and token verification for the agent.

* IdentityProvider.login() exchanges username/password for an access token
  (Amazon Cognito USER_PASSWORD_AUTH in the cloud, an HS256 development token
  locally). The agent never stores passwords.
* TokenVerifier.verify() validates the bearer token on every agent request.
  The API Gateway has already checked it; the agent checks again because it
  uses the token's identity to decide whose bill it may read.

The subscriber number always comes from the token's `username` claim, never
from the user's prompt or from LLM tool arguments.
"""

import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx
import jwt

from app.config import Settings

logger = logging.getLogger("agent.identity")


class AuthError(Exception):
    def __init__(self, detail: str, status: int = 401) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    expires_in: int


@dataclass(frozen=True)
class UserContext:
    username: str
    groups: frozenset[str]
    access_token: str
    request_id: str = ""
    conversation_id: str = ""

    @property
    def subscriber_no(self) -> str:
        return self.username


class IdentityProvider(Protocol):
    async def login(self, username: str, password: str) -> TokenSet: ...


class TokenVerifier(Protocol):
    def verify(self, token: str) -> UserContext: ...


# ---------- local development ----------

class LocalIdentityProvider:
    """Issues tokens with the same claims as a Cognito access token."""

    def __init__(self, users: dict[str, dict], secret: str, issuer: str, ttl_seconds: int = 3600) -> None:
        self._users = users
        self._secret = secret
        self._issuer = issuer
        self._ttl = ttl_seconds

    async def login(self, username: str, password: str) -> TokenSet:
        user = self._users.get(username)
        if user is None or not hmac.compare_digest(str(user["password"]), password):
            raise AuthError("Incorrect username or password.")
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": f"local-{username}",
                "username": username,
                "cognito:groups": user.get("groups", []),
                "token_use": "access",
                "client_id": "local-agent",
                "iss": self._issuer,
                "iat": now,
                "exp": now + timedelta(seconds=self._ttl),
            },
            self._secret,
            algorithm="HS256",
        )
        return TokenSet(access_token=token, expires_in=self._ttl)


def _context_from_claims(claims: dict, token: str) -> UserContext:
    if claims.get("token_use") != "access" or not claims.get("username"):
        raise AuthError("An access token with a username is required.")
    return UserContext(username=str(claims["username"]), groups=frozenset(claims.get("cognito:groups") or []),
                       access_token=token)


class LocalTokenVerifier:
    def __init__(self, secret: str, issuer: str) -> None:
        self._secret = secret
        self._issuer = issuer

    def verify(self, token: str) -> UserContext:
        try:
            claims = jwt.decode(token, self._secret, algorithms=["HS256"], issuer=self._issuer,
                                options={"require": ["exp", "iss", "username"]})
        except jwt.PyJWTError as exc:
            raise AuthError("Token is invalid or expired.") from exc
        return _context_from_claims(claims, token)


# ---------- Amazon Cognito ----------

class CognitoIdentityProvider:
    """Calls the public Cognito InitiateAuth API (no AWS credentials needed for a public app client)."""

    def __init__(self, region: str, client_id: str, timeout: float = 10.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._url = f"https://cognito-idp.{region}.amazonaws.com/"
        self._client_id = client_id
        self._timeout = timeout
        self._transport = transport

    async def login(self, username: str, password: str) -> TokenSet:
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.post(
                self._url,
                headers={
                    "Content-Type": "application/x-amz-json-1.1",
                    "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
                },
                json={
                    "AuthFlow": "USER_PASSWORD_AUTH",
                    "ClientId": self._client_id,
                    "AuthParameters": {"USERNAME": username, "PASSWORD": password},
                },
            )
        body = response.json() if response.content else {}
        if response.status_code != 200:
            # The client only learns "incorrect username or password"; the log keeps the real reason
            # (e.g. NotAuthorizedException vs. a misconfigured app client). The password is never logged.
            logger.warning("Cognito sign-in failed for %s: %s %s", username, response.status_code,
                           body.get("__type", "unknown"))
            raise AuthError("Incorrect username or password.")
        if "AuthenticationResult" not in body:
            # e.g. NEW_PASSWORD_REQUIRED: demo users are created with permanent passwords.
            raise AuthError(f"Additional sign-in step required: {body.get('ChallengeName', 'unknown')}.")
        result = body["AuthenticationResult"]
        return TokenSet(access_token=result["AccessToken"], expires_in=int(result.get("ExpiresIn", 3600)))


class CognitoTokenVerifier:
    def __init__(self, issuer: str, client_id: str) -> None:
        self._issuer = issuer
        self._client_id = client_id
        self._jwks = jwt.PyJWKClient(f"{issuer}/.well-known/jwks.json", cache_keys=True, lifespan=3600)

    def verify(self, token: str) -> UserContext:
        try:
            key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(token, key.key, algorithms=["RS256"], issuer=self._issuer,
                                options={"require": ["exp", "iss", "client_id"], "verify_aud": False})
        except jwt.PyJWTError as exc:
            raise AuthError("Token is invalid or expired.") from exc
        if claims.get("client_id") != self._client_id:
            raise AuthError("Token was issued for another client.")
        return _context_from_claims(claims, token)


def build_identity(settings: Settings) -> tuple[IdentityProvider, TokenVerifier]:
    if settings.auth_mode == "cognito":
        return (CognitoIdentityProvider(settings.cognito_region, settings.cognito_app_client_id),
                CognitoTokenVerifier(settings.cognito_issuer, settings.cognito_app_client_id))
    return (LocalIdentityProvider(settings.local_users, settings.jwt_secret, settings.jwt_issuer),
            LocalTokenVerifier(settings.jwt_secret, settings.jwt_issuer))
