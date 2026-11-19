"""Bearer-token protection of the HTTP transport (in-process ASGI, no network)."""

from __future__ import annotations

import asyncio
import logging

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


def _post_init(app, headers):
    async def run():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
                return await http.post("/mcp", json=INIT, headers={**HEADERS, **headers})

    return asyncio.run(run())


def test_cloud_with_token_enforces_it(monkeypatch, config):
    from ebay_mcp.cloud import build_cloud_server

    monkeypatch.setenv("MCP_AUTH_TOKEN", TOKEN)
    server = build_cloud_server(config)
    assert isinstance(server.auth, BearerTokenVerifier)
    app = server.http_app(path="/mcp")
    assert _post_init(app, {}).status_code == 401
    assert _post_init(app, {"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_cloud_with_short_token_still_refused(monkeypatch, config):
    from ebay_mcp.cloud import build_cloud_server

    monkeypatch.setenv("MCP_AUTH_TOKEN", "short")
    with pytest.raises(AuthConfigError, match="at least"):
        build_cloud_server(config)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_cloud_without_token_relies_on_platform_auth(monkeypatch, caplog, config, value):
    from ebay_mcp.cloud import build_cloud_server

    if value is None:
        monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    else:
        monkeypatch.setenv("MCP_AUTH_TOKEN", value)
    with caplog.at_level(logging.INFO, logger="ebay_mcp.cloud"):
        server = build_cloud_server(config)
    assert server.auth is None
    records = [r for r in caplog.records if r.name == "ebay_mcp.cloud"]
    assert len(records) == 1
    assert "relying on the hosting platform's auth" in records[0].getMessage()
    # The platform's proxied request (no bearer token) is accepted.
    assert _post_init(server.http_app(path="/mcp"), {}).status_code == 200


def test_cloud_module_imports_without_token(monkeypatch):
    import importlib
    import sys

    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    sys.modules.pop("ebay_mcp.cloud", None)
    try:
        module = importlib.import_module("ebay_mcp.cloud")
        assert module.mcp.auth is None
    finally:
        sys.modules.pop("ebay_mcp.cloud", None)


def test_local_http_serve_still_requires_token(monkeypatch):
    from ebay_mcp.cli import main

    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    assert main(["serve", "--transport", "http"]) == 2
    monkeypatch.setenv("MCP_AUTH_TOKEN", "x" * 31)
    assert main(["serve", "--transport", "http"]) == 2
