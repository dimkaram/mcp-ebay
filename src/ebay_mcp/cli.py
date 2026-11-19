"""Command-line interface.

Subcommands:

* ``serve``  -- run the MCP server (stdio by default, or ``--transport http``).
* ``search`` -- run ``search_listings`` once and print its JSON.
* ``item``   -- run ``get_listing_details`` once and print its JSON.
* ``stats``  -- run ``price_statistics`` over item ids.
* ``check``  -- verify eBay credentials by requesting an OAuth token.

``search`` and ``item`` also take ``--raw`` to print eBay's unprocessed JSON,
which is how the field checkpoint (``docs/field-checkpoint.md``) was done.
Non-raw commands call the real MCP tools in memory, so the output is exactly
what Claude receives.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

from . import __version__
from .client import EbayClient
from .config import Config, ConfigError
from .errors import EbayError

_SORTS = ["best_match", "price_asc", "price_desc", "newly_listed", "ending_soonest"]
_CONDITIONS = ["new", "new_other", "refurbished", "used", "for_parts"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-ebay",
        description="Read-only eBay deal-hunting data for Claude over the Model Context Protocol.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="run the MCP server")
    p_serve.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    p_serve.add_argument("--host", default=os.environ.get("MCP_HOST", "127.0.0.1"))
    p_serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    p_serve.add_argument("--path", default="/mcp")
    p_serve.set_defaults(func=_cmd_serve)

    p_search = sub.add_parser("search", help="run search_listings once")
    p_search.add_argument("keywords")
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--sort", default="best_match", choices=_SORTS)
    p_search.add_argument("--condition", action="append", choices=_CONDITIONS, default=None)
    p_search.add_argument(
        "--format", dest="buying_format", choices=["any", "buy_it_now", "auction"], default="any"
    )
    p_search.add_argument("--max-total", type=float, default=None)
    p_search.add_argument("--raw", action="store_true", help="print eBay's raw JSON")
    p_search.add_argument("--filter", default=None, help="raw mode: eBay filter string")
    p_search.add_argument("--fieldgroups", default=None, help="raw mode: e.g. EXTENDED")
    p_search.set_defaults(func=_cmd_search)

    p_item = sub.add_parser("item", help="run get_listing_details once")
    p_item.add_argument("item_id")
    p_item.add_argument("--raw", action="store_true", help="print eBay's raw JSON")
    p_item.add_argument("--fieldgroups", default=None, help="raw mode: e.g. PRODUCT")
    p_item.set_defaults(func=_cmd_item)

    p_stats = sub.add_parser("stats", help="run price_statistics over item ids")
    p_stats.add_argument("item_ids", nargs="+")
    p_stats.set_defaults(func=_cmd_stats)

    p_check = sub.add_parser("check", help="verify credentials by requesting a token")
    p_check.set_defaults(func=_cmd_check)

    return parser


def _cmd_serve(args: argparse.Namespace) -> int:
    from .server import build_server, logging_setup

    config = Config.load()
    if args.transport == "stdio":
        logging_setup(logging.WARNING)
        build_server(config).run(transport="stdio", show_banner=False)
        return 0

    from .http_auth import AuthConfigError, verifier_from_env

    try:
        auth = verifier_from_env()
    except AuthConfigError as exc:
        print(json.dumps({"error": "AuthConfigError", "message": str(exc)}), file=sys.stderr)
        return 2
    logging_setup()
    build_server(config, auth=auth).run(
        transport="http", host=args.host, port=args.port, path=args.path, show_banner=False
    )
    return 0


async def _call_tool(name: str, arguments: dict[str, Any]) -> int:
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from .server import build_server

    server = build_server(Config.load())
    async with Client(server) as client:
        try:
            result = await client.call_tool(name, arguments)
        except ToolError as exc:
            print(json.dumps({"error": "ToolError", "message": str(exc)}), file=sys.stderr)
            return 1
    print(json.dumps(result.structured_content, indent=2, ensure_ascii=False))
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    if args.raw:

        async def raw() -> int:
            params: dict[str, Any] = {"q": args.keywords, "limit": args.limit}
            if args.fieldgroups:
                params["fieldgroups"] = args.fieldgroups
            if args.filter:
                params["filter"] = args.filter
            async with EbayClient(Config.load()) as client:
                data = await client._get("/item_summary/search", params=params)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            return 0

        return _run(raw())

    arguments: dict[str, Any] = {
        "keywords": args.keywords,
        "limit": args.limit,
        "sort": args.sort,
        "buying_format": args.buying_format,
    }
    if args.condition:
        arguments["conditions"] = args.condition
    if args.max_total is not None:
        arguments["max_total"] = args.max_total
    return _run(_call_tool("search_listings", arguments))


def _cmd_item(args: argparse.Namespace) -> int:
    if args.raw:

        async def raw() -> int:
            from .client import _item_path

            params = {"fieldgroups": args.fieldgroups} if args.fieldgroups else None
            async with EbayClient(Config.load()) as client:
                data = await client._get(_item_path(args.item_id), params=params)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            return 0

        return _run(raw())
    return _run(_call_tool("get_listing_details", {"item_id": args.item_id}))


def _cmd_stats(args: argparse.Namespace) -> int:
    return _run(_call_tool("price_statistics", {"item_ids": args.item_ids}))


def _cmd_check(args: argparse.Namespace) -> int:
    import httpx

    from .auth import TokenManager

    async def run() -> int:
        config = Config.load()
        config.require_credentials()
        async with httpx.AsyncClient() as http:
            await TokenManager(config, http).get_token()
        print(
            json.dumps(
                {
                    "ok": True,
                    "environment": config.environment,
                    "marketplace_id": config.marketplace_id,
                    "item_location_country": config.item_location_country,
                    "delivery_postcode_configured": bool(config.delivery_postal_code),
                },
                indent=2,
            )
        )
        return 0

    return _run(run())


def _run(coro) -> int:
    try:
        return asyncio.run(coro)
    except (EbayError, ConfigError) as exc:
        payload = json.dumps({"error": type(exc).__name__, "message": str(exc)}, indent=2)
        print(payload, file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s: %(message)s")
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
