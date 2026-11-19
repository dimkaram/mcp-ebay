"""Shared test fixtures.

``tests/fixtures/*_raw.json`` are real eBay Browse API responses (EBAY_GB,
captured 2026-11-19 with ``mcp-ebay search/item --raw``). Tests never call eBay:
:class:`FakeEbay` serves these files through ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import pytest

from ebay_mcp.client import EbayClient
from ebay_mcp.config import Config

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def summary(name: str, item_id: str) -> dict[str, Any]:
    return next(i for i in load(name)["itemSummaries"] if i["itemId"] == item_id)


@pytest.fixture
def config() -> Config:
    """A fully-populated config that never touches the network."""

    return Config(
        client_id="test-client-id",
        client_secret="test-client-secret",
        environment="sandbox",
        marketplace_id="EBAY_GB",
        max_retries=3,
        delivery_country="GB",
        delivery_postal_code="SW1A 1AA",
        item_location_country="GB",
    )


class FakeEbay:
    """A scriptable eBay backend for ``httpx.MockTransport``.

    ``search`` maps a query string to a fixture name (or a dict). Items are
    served from ``items`` (item id -> fixture name or dict); unknown ids 404.
    """

    def __init__(
        self,
        *,
        search: dict[str, str | dict[str, Any]] | None = None,
        items: dict[str, str | dict[str, Any]] | None = None,
        search_status: list[int] | None = None,
    ) -> None:
        self.search = search or {}
        self.items = items or {}
        self.search_status = search_status or [200]
        self.search_requests: list[httpx.Request] = []
        self.item_requests: list[str] = []
        self._search_calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = urlparse(str(request.url))
        if url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 7200})
        if url.path.endswith("/item_summary/search"):
            self.search_requests.append(request)
            status = self.search_status[min(self._search_calls, len(self.search_status) - 1)]
            self._search_calls += 1
            if status != 200:
                return httpx.Response(status, json={"errors": [{"message": f"boom {status}"}]})
            q = parse_qs(url.query)["q"][0]
            body = self.search.get(q, {"total": 0})
            return httpx.Response(200, json=load(body) if isinstance(body, str) else body)
        if "/item/" in url.path:
            item_id = unquote(url.path.split("/item/", 1)[1])
            self.item_requests.append(item_id)
            body = self.items.get(item_id)
            if body is None:
                return httpx.Response(404, json={"errors": [{"message": "Item not found"}]})
            return httpx.Response(200, json=load(body) if isinstance(body, str) else body)
        return httpx.Response(404, json={"errors": [{"message": "not found"}]})

    def client(self, config: Config) -> EbayClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        client = EbayClient(config, http=http)

        async def _no_sleep(*args, **kwargs):
            return None

        client._backoff = _no_sleep  # type: ignore[method-assign]
        return client


@pytest.fixture
def listing_json():
    """Factory for synthetic eBay summary JSON (for edge cases fixtures lack)."""

    def _make(
        *,
        item_id: str = "v1|1|0",
        price: float | None = 100.0,
        postage: float | None = 5.0,
        buying_options: tuple[str, ...] = ("FIXED_PRICE",),
        current_bid: float | None = None,
        country: str = "GB",
        currency: str = "GBP",
        condition: str = "Used",
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "itemId": item_id,
            "title": f"Item {item_id}",
            "condition": condition,
            "buyingOptions": list(buying_options),
            "itemLocation": {"country": country},
        }
        if price is not None:
            data["price"] = {"value": f"{price:.2f}", "currency": currency}
        if current_bid is not None:
            data["currentBidPrice"] = {"value": f"{current_bid:.2f}", "currency": currency}
        if postage is not None:
            data["shippingOptions"] = [
                {
                    "shippingCostType": "FIXED",
                    "shippingCost": {"value": f"{postage:.2f}", "currency": currency},
                }
            ]
        return data

    return _make
