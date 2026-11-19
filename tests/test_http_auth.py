"""Bearer-token protection of the HTTP transport (in-process ASGI, no network)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from conftest import FakeEbay

from ebay_mcp.http_auth import AuthConfigError, BearerTokenVerifier, verifier_from_env
from ebay_mcp.server import build_server

TOKEN = "t" * 40

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_verifier_from_env(monkeypatch):
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    with pytest.raises(AuthConfigError, match="not set"):
        verifier_from_env()
    monkeypatch.setenv("MCP_AUTH_TOKEN", "short")
    with pytest.raises(AuthConfigError, match="at least"):
        verifier_from_env()
    monkeypatch.setenv("MCP_AUTH_TOKEN", TOKEN)
    assert isinstance(verifier_from_env(), BearerTokenVerifier)


def test_verifier_compares_tokens():
    verifier = BearerTokenVerifier(TOKEN)
    assert asyncio.run(verifier.verify_token(TOKEN)) is not None
    assert asyncio.run(verifier.verify_token(TOKEN + "x")) is None
    assert asyncio.run(verifier.verify_token("")) is None


def test_http_app_requires_token(config):
    server = build_server(config, FakeEbay().client(config), auth=BearerTokenVerifier(TOKEN))
    app = server.http_app(path="/mcp")

    async def run():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
                missing = await http.post("/mcp", json=INIT, headers=HEADERS)
                wrong = await http.post(
                    "/mcp", json=INIT, headers={**HEADERS, "Authorization": "Bearer nope"}
                )
                good = await http.post(
                    "/mcp", json=INIT, headers={**HEADERS, "Authorization": f"Bearer {TOKEN}"}
                )
        return missing, wrong, good

    missing, wrong, good = asyncio.run(run())
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert good.status_code == 200
    assert "search_listings" not in missing.text


def test_cloud_entrypoint_fails_closed(monkeypatch):
    import importlib
    import sys

    sys.modules.pop("ebay_mcp.cloud", None)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    with pytest.raises(AuthConfigError):
        importlib.import_module("ebay_mcp.cloud")

    sys.modules.pop("ebay_mcp.cloud", None)
    monkeypatch.setenv("MCP_AUTH_TOKEN", TOKEN)
    module = importlib.import_module("ebay_mcp.cloud")
    assert isinstance(module.mcp.auth, BearerTokenVerifier)
    sys.modules.pop("ebay_mcp.cloud", None)
