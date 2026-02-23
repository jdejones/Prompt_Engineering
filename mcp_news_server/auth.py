"""OAuth resource-server helpers for MCP."""

from __future__ import annotations

import logging
from typing import Any

import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings

from mcp_news_server.config import Settings

LOGGER = logging.getLogger(__name__)


class JwtTokenVerifier(TokenVerifier):
    """Validate OAuth access tokens against issuer, audience, and scopes."""

    def __init__(
        self,
        jwks_uri: str,
        issuer: str,
        audience: str,
        required_scopes: list[str],
    ) -> None:
        self._jwks_client = jwt.PyJWKClient(jwks_uri)
        self._issuer = issuer
        self._audience = audience
        self._required_scopes = set(required_scopes)

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iss"]},
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Token verification failed: %s", exc)
            return None

        scopes = _extract_scopes(claims)
        if self._required_scopes and not self._required_scopes.issubset(set(scopes)):
            LOGGER.info("Token missing required scopes. required=%s got=%s", sorted(self._required_scopes), scopes)
            return None

        audience = claims.get("aud")
        resource = audience[0] if isinstance(audience, list) and audience else audience
        client_id = str(claims.get("client_id") or claims.get("azp") or claims.get("sub") or "unknown")

        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(claims["exp"]) if "exp" in claims else None,
            resource=str(resource) if resource else None,
        )


def _extract_scopes(claims: dict[str, Any]) -> list[str]:
    raw_scope = claims.get("scope")
    if isinstance(raw_scope, str):
        return [scope for scope in raw_scope.split() if scope]
    if isinstance(raw_scope, list):
        return [str(scope) for scope in raw_scope]

    raw_scp = claims.get("scp")
    if isinstance(raw_scp, str):
        return [scope for scope in raw_scp.split() if scope]
    if isinstance(raw_scp, list):
        return [str(scope) for scope in raw_scp]
    return []


def build_auth_settings(settings: Settings) -> AuthSettings:
    """Build MCP auth metadata used for RFC 9728 discovery."""
    return AuthSettings(
        issuer_url=settings.auth_issuer_url,
        resource_server_url=settings.mcp_base_url,
        required_scopes=settings.auth_required_scopes,
    )


def build_token_verifier(settings: Settings) -> JwtTokenVerifier:
    return JwtTokenVerifier(
        jwks_uri=settings.auth_jwks_uri,
        issuer=settings.auth_issuer_url,
        audience=settings.auth_audience,
        required_scopes=settings.auth_required_scopes,
    )
