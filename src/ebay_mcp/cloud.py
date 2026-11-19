"""Entrypoint for FastMCP Cloud (or any host that imports a server object).

Point the host at ``src/ebay_mcp/cloud.py:mcp``. The host runs the HTTP
transport itself; this module attaches bearer-token auth from
``MCP_AUTH_TOKEN`` and fails at import if the token is missing, so the server
can never come up unauthenticated.
"""

from __future__ import annotations

from ebay_mcp.config import Config
from ebay_mcp.http_auth import verifier_from_env
from ebay_mcp.server import build_server

mcp = build_server(Config.load(), auth=verifier_from_env())
