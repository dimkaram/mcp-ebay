"""EbayClient: request construction, retries and parsing (MockTransport, no network)."""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import FakeEbay

from ebay_mcp.client import EbayClient
from ebay_mcp.errors import EbayAPIError, EbayError


def _params(fake: FakeEbay) -> dict[str, list[str]]:
    return parse_qs(urlparse(str(fake.search_requests[-1].url)).query)


def _search(config, fake: FakeEbay, query: str = "switch", **kwargs):
    async def run():
        async with fake.client(config) as client:
            return await client.search(query, **kwargs)

    return asyncio.run(run())


def test_search_parses_fixture_and_sets_headers(config):
    fake = FakeEbay(search={"switch": "search_fixed_raw"})
    result = _search(config, fake, limit=20)
    assert result.total == 3401 and len(result.listings) == 20
    request = fake.search_requests[-1]
    assert request.headers["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_GB"
    assert request.headers["Authorization"] == "Bearer tok"
    # Postcode with a space is URL-encoded inside the context header.
    assert request.headers["X-EBAY-C-ENDUSERCTX"] == (
        "contextualLocation=country%3DGB%2Czip%3DSW1A%201AA"
    )
    assert _params(fake)["limit"] == ["20"]


def test_search_filter_grammar(config):
    fake = FakeEbay()
    _search(
        config,
        fake,
        conditions=["used", "refurbished"],
        buying_format="auction",
        max_item_price=150,
        location_country="gb",
        sort="ending_soonest",
    )
    params = _params(fake)
    clauses = params["filter"][0].split(",")
    assert "price:[..150]" in clauses
    assert "priceCurrency:GBP" in clauses
    assert "conditionIds:{2750|3000|4000|5000|6000|2000|2010|2020|2030|2500}" in clauses
    assert "buyingOptions:{AUCTION}" in clauses
    assert "itemLocationCountry:GB" in clauses
    assert params["sort"] == ["endingSoonest"]


def test_search_without_filters_sends_none(config):
    fake = FakeEbay()
    _search(config, fake)
    params = _params(fake)
    assert "filter" not in params and "sort" not in params


def test_buy_it_now_format(config):
    fake = FakeEbay()
    _search(config, fake, buying_format="buy_it_now")
    assert _params(fake)["filter"] == ["buyingOptions:{FIXED_PRICE}"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sort": "cheapest"},
        {"conditions": ["mint"]},
        {"buying_format": "raffle"},
        {"max_item_price": 0},
    ],
)
def test_invalid_arguments_rejected(config, kwargs):
    with pytest.raises(EbayError):
        _search(config, FakeEbay(), **kwargs)


def test_empty_query_rejected(config):
    with pytest.raises(EbayError):
        _search(config, FakeEbay(), query="   ")


def test_retries_then_succeeds_on_500(config):
    fake = FakeEbay(search={"switch": "search_fixed_raw"}, search_status=[500, 500, 200])
    assert _search(config, fake).total == 3401
    assert len(fake.search_requests) == 3


def test_gives_up_after_max_retries(config):
    fake = FakeEbay(search_status=[503])
    with pytest.raises(EbayAPIError) as info:
        _search(config, fake)
    assert info.value.status_code == 503


def test_get_item_and_listing(config):
    fake = FakeEbay(items={"v1|168858079790|0": "item_auction_raw"})

    async def run():
        async with fake.client(config) as client:
            details = await client.get_item("v1|168858079790|0")
            listing = await client.get_listing("v1|168858079790|0")
            with pytest.raises(EbayAPIError):
                await client.get_item("v1|404|0")
            with pytest.raises(EbayError):
                await client.get_item(" ")
        return details, listing

    details, listing = asyncio.run(run())
    assert details.listing.is_auction and details.unique_bidders == 5
    assert listing.item_id == "v1|168858079790|0"
    assert fake.item_requests[:2] == ["v1|168858079790|0"] * 2


def test_no_delivery_header_without_country(config):
    from dataclasses import replace

    fake = FakeEbay()
    _search(replace(config, delivery_country=None), fake)
    assert "X-EBAY-C-ENDUSERCTX" not in fake.search_requests[-1].headers


def test_client_is_read_only():
    # Only GET helpers exist: no bid/buy/offer/order surface.
    public = {name for name in dir(EbayClient) if not name.startswith("_")}
    assert public == {"aclose", "build_filters", "get_item", "get_listing", "search"}
