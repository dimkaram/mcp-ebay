"""Entrypoint for hosted platforms that import a server object
(Prefect Horizon / FastMCP Cloud).

Point the host at ``src/ebay_mcp/cloud.py:mcp``. The host runs the HTTP
transport itself.

Auth:

* ``MCP_AUTH_TOKEN`` set: the server enforces it as a bearer token (32+
  characters, same rules as ``mcp-ebay serve --transport http``).
* ``MCP_AUTH_TOKEN`` unset: no app-level auth is added and the server relies on
  the platform's auth. Horizon puts OAuth in front of the server and proxies to
  the container without passing a token we could check, so on Horizon the
  token must be left unset.
"""

from __future__ import annotations

import logging
import os

from fastmcp import FastMCP

from ebay_mcp.config import Config
from ebay_mcp.http_auth import TOKEN_ENV, verifier_from_env
from ebay_mcp.server import build_server

log = logging.getLogger("ebay_mcp.cloud")


def build_cloud_server(config: Config | None = None) -> FastMCP:
    """Build the hosted server, enforcing ``MCP_AUTH_TOKEN`` only when it is set."""

    if os.environ.get(TOKEN_ENV, "").strip():
        return build_server(config or Config.load(), auth=verifier_from_env())
    log.warning("%s not set: no app-level auth; relying on the hosting platform's auth.", TOKEN_ENV)
    return build_server(config or Config.load())


mcp = build_cloud_server()
