"""The MCP tools end to end, in memory, against saved eBay fixtures."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from conftest import FakeEbay
from fastmcp import Client
from fastmcp.exceptions import ToolError

from ebay_mcp.instructions import INSTRUCTIONS
from ebay_mcp.server import build_server

NOW = datetime(2026, 11, 19, 2, 0, tzinfo=UTC)


def _server(config, fake: FakeEbay):
    return build_server(config, fake.client(config), clock=lambda: NOW)


def _call(server, *calls):
    """Run ``(tool, args)`` calls in one session; return structured results."""

    async def run():
        async with Client(server) as client:
            out = []
            for name, args in calls:
                out.append((await client.call_tool(name, args)).structured_content)
            return out

    return asyncio.run(run())


def test_tools_and_instructions(config):
    server = _server(config, FakeEbay())

    async def run():
        async with Client(server) as client:
            return await client.list_tools(), client.instructions

    tools, instructions = asyncio.run(run())
    assert {t.name for t in tools} == {"search_listings", "get_listing_details", "price_statistics"}
    assert all(t.annotations.read_only_hint for t in tools)
    assert instructions == INSTRUCTIONS
    assert "untrusted" in INSTRUCTIONS and "price_statistics" in INSTRUCTIONS


def test_search_listings_applies_true_total(config):
    fake = FakeEbay(search={"switch oled": "search_fixed_raw"})
    (result,) = _call(
        _server(config, fake), ("search_listings", {"keywords": "switch oled", "max_total": 120})
    )
    assert result["total_matches"] == 3401
    assert result["returned"] == 5
    assert result["dropped"] == {"over_max_total": 15, "outside_location": 0}
    assert all(i["total_cost"] <= 120 and i["budget"] == "within" for i in result["items"])
    assert result["filters"]["item_location_country"] == "GB"
    assert "itemLocationCountry:GB" in str(fake.search_requests[-1].url.params["filter"])


def test_search_listings_auction_fields(config):
    fake = FakeEbay(search={"switch": "search_auction_raw"})
    (result,) = _call(_server(config, fake), ("search_listings", {"keywords": "switch"}))
    item = next(i for i in result["items"] if i["item_id"] == "v1|168858079790|0")
    assert item["format"] == "auction" and item["price"] is None
    assert item["auction"]["current_bid"] == 102.08
    assert item["auction"]["time_left"] == "20h 10m"


def test_search_listings_postage_unknown(config):
    fake = FakeEbay(search={"sofa": "search_sofa_raw"})
    (result,) = _call(
        _server(config, fake), ("search_listings", {"keywords": "sofa", "max_total": 500})
    )
    unknown = [i for i in result["items"] if i["postage"]["status"] == "unknown"]
    assert result["postage_unknown"] == len(unknown) == 4  # collection-only under £500
    assert all(i["budget"] == "postage_unknown" and i["total_cost"] is None for i in unknown)


def test_details_groups_seller_text_as_untrusted(config):
    fake = FakeEbay(items={"v1|920157680853|0": "item_fixed_raw"})
    (details,) = _call(
        _server(config, fake), ("get_listing_details", {"item_id": "v1|920157680853|0"})
    )
    assert details["seller_content_untrusted"]["item_specifics"]["Platform"] == "Nintendo Switch"
    assert details["returns"]["accepted"] is False
    assert details["total_cost"] == 103.37


def test_price_statistics_uses_cache_then_falls_back(config):
    fake = FakeEbay(
        search={"switch": "search_fixed_raw"},
        items={"v1|168858079790|0": "item_auction_raw"},
    )
    server = _server(config, fake)
    ids = [
        "v1|920157680853|0",
        "v1|920153991104|0",
        "v1|364710736005|634243887933",
    ]
    search, stats = _call(
        server,
        ("search_listings", {"keywords": "switch", "limit": 20}),
        ("price_statistics", {"item_ids": ids + ["v1|168858079790|0", "v1|404|0"]}),
    )
    assert len(search["items"]) == 20
    # Three came from the cache; the auction and the unknown id were fetched.
    assert fake.item_requests == ["v1|168858079790|0", "v1|404|0"] or sorted(
        fake.item_requests
    ) == sorted(["v1|168858079790|0", "v1|404|0"])
    assert stats["fetched_from_ebay"] == 2
    assert stats["count"] == 3 and stats["low_confidence"] is True
    reasons = {e["item_id"]: e["reason"] for e in stats["excluded"]}
    assert reasons["v1|168858079790|0"].startswith("auction only")
    assert reasons["v1|404|0"] == "could not fetch (HTTP 404)"


def test_price_statistics_figures_with_enough_comparables(config):
    fake = FakeEbay(search={"switch": "search_fixed_raw"})
    server = _server(config, fake)

    async def run():
        async with Client(server) as client:
            search = await client.call_tool("search_listings", {"keywords": "switch"})
            ids = [i["item_id"] for i in search.structured_content["items"]][:8]
            return (
                await client.call_tool("price_statistics", {"item_ids": ids})
            ).structured_content

    stats = asyncio.run(run())
    assert stats["count"] == 8 and stats["fetched_from_ebay"] == 0
    assert stats["p25"] <= stats["median"] <= stats["max"]
    assert fake.item_requests == []


def test_ebay_errors_become_tool_errors(config):
    fake = FakeEbay(search_status=[400])
    with pytest.raises(ToolError, match="HTTP 400"):
        _call(_server(config, fake), ("search_listings", {"keywords": "x"}))
    with pytest.raises(ToolError):
        _call(_server(config, fake), ("get_listing_details", {"item_id": "v1|404|0"}))


def test_invalid_arguments_rejected_by_schema(config):
    with pytest.raises(ToolError):
        _call(_server(config, FakeEbay()), ("search_listings", {"keywords": "x", "limit": 500}))
    with pytest.raises(ToolError):
        _call(
            _server(config, FakeEbay()),
            ("search_listings", {"keywords": "x", "buying_format": "bid_now"}),
        )
