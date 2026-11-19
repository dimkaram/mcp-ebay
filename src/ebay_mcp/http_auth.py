"""Bearer-token protection for the HTTP transport.

The token is read from ``MCP_AUTH_TOKEN`` and compared in constant time. Clients
send ``Authorization: Bearer <token>``. The stdio transport needs no token.
"""

from __future__ import annotations

import hmac
import os

from fastmcp.server.auth import AccessToken, TokenVerifier

TOKEN_ENV = "MCP_AUTH_TOKEN"
MIN_TOKEN_LENGTH = 32


class AuthConfigError(RuntimeError):
    """The HTTP transport was started without a usable token."""


class BearerTokenVerifier(TokenVerifier):
    """Accept exactly one shared secret token."""

    def __init__(self, token: str) -> None:
        super().__init__()
        if len(token) < MIN_TOKEN_LENGTH:
            raise AuthConfigError(
                f"{TOKEN_ENV} must be at least {MIN_TOKEN_LENGTH} characters "
                "(generate one with: python -c 'import secrets; print(secrets.token_urlsafe(32))')."
            )
        self._token = token.encode()

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token or not hmac.compare_digest(token.encode(), self._token):
            return None
        return AccessToken(token="redacted", client_id="mcp-ebay-client", scopes=[])


def verifier_from_env() -> BearerTokenVerifier:
    """Build the verifier from ``MCP_AUTH_TOKEN``; refuse to run without one."""

    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        raise AuthConfigError(
            f"{TOKEN_ENV} is not set. The HTTP transport refuses to start unauthenticated."
        )
    return BearerTokenVerifier(token)
