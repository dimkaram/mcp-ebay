"""Budget check, location filter and comparables statistics."""

from __future__ import annotations

import pytest
from conftest import load

from ebay_mcp import analysis
from ebay_mcp.models import Listing, SearchResult


@pytest.fixture
def make(listing_json):
    return lambda **kw: Listing.from_api(listing_json(**kw))


def test_percentile_linear_interpolation():
    assert analysis.percentile([10, 20, 30, 40, 50], 25) == 20
    assert analysis.percentile([10, 20, 30, 40], 25) == 17.5
    assert analysis.percentile([7], 25) == 7
    assert analysis.percentile([], 25) is None


def test_budget_uses_true_total_not_item_price(make):
    # Item price fits, but item + postage does not.
    assert analysis.check_budget(make(price=98, postage=5), 100).status == "over"
    assert analysis.check_budget(make(price=95, postage=5), 100).status == "within"


def test_budget_unknown_postage_kept_and_marked(make):
    assert analysis.check_budget(make(price=90, postage=None), 100).status == "postage_unknown"
    assert analysis.check_budget(make(price=120, postage=None), 100).status == "over"


def test_budget_auctions_use_current_bid_plus_postage(make):
    auction = make(price=None, current_bid=94, postage=5, buying_options=("AUCTION",))
    assert analysis.check_budget(auction, 100).status == "within"
    over = make(price=None, current_bid=97, postage=5, buying_options=("AUCTION",))
    assert analysis.check_budget(over, 100).status == "over"


def test_budget_auction_with_bin_over_budget_keeps_auction_path(make):
    listing = make(price=150, current_bid=60, postage=5, buying_options=("FIXED_PRICE", "AUCTION"))
    check = analysis.check_budget(listing, 100)
    assert check.status == "within"
    assert "Buy It Now total is over budget" in check.note


def test_apply_max_total_on_fixture():
    listings = SearchResult.from_api(load("search_fixed_raw")).listings
    kept, dropped = analysis.apply_max_total(listings, 120)
    totals = sorted(listing.total_cost for listing, _ in kept)
    assert totals == [8.99, 67.5, 93.37, 103.37, 119.89]
    assert dropped == 15
    assert all(check.status == "within" for _, check in kept)


def test_apply_max_total_none_keeps_everything(make):
    kept, dropped = analysis.apply_max_total([make(), make(item_id="v1|2|0")], None)
    assert len(kept) == 2 and dropped == 0 and kept[0][1] is None


def test_filter_location(make):
    kept, dropped = analysis.filter_location(
        [make(country="GB"), make(item_id="v1|2|0", country="CN")], "GB"
    )
    assert [listing.location_country for listing in kept] == ["GB"]
    assert dropped == 1
    assert analysis.filter_location([make(country="CN")], None) == ([make(country="CN")], 0)


def _by_id(listings):
    return {listing.item_id: listing for listing in listings}


def test_comparable_statistics_full_sample(make):
    prices = [95, 105, 115, 125, 135]
    listings = [make(item_id=f"v1|{i}|0", price=p, postage=5) for i, p in enumerate(prices)]
    stats = analysis.comparable_statistics([x.item_id for x in listings], _by_id(listings))
    assert stats["count"] == 5
    assert stats["low_confidence"] is False
    assert stats["p25"] == 110.0
    assert stats["median"] == 120.0
    assert stats["min"] == 100.0 and stats["max"] == 140.0
    assert [p["total_cost"] for p in stats["prices"]] == [100, 110, 120, 130, 140]


def test_comparable_statistics_withholds_figures_below_min_sample(make):
    listings = [make(item_id=f"v1|{i}|0", price=100 + i) for i in range(4)]
    stats = analysis.comparable_statistics([x.item_id for x in listings], _by_id(listings))
    assert stats["low_confidence"] is True
    assert "p25" not in stats and "median" not in stats
    assert len(stats["prices"]) == 4


def test_comparable_statistics_exclusions(make):
    listings = [
        make(item_id="v1|1|0"),
        make(item_id="v1|2|0", price=None, current_bid=50, buying_options=("AUCTION",)),
        make(item_id="v1|3|0", postage=None),
        make(item_id="v1|4|0", currency="EUR"),
    ]
    stats = analysis.comparable_statistics(
        ["v1|1|0", "v1|2|0", "v1|3|0", "v1|4|0", "v1|9|0", "v1|1|0"],
        _by_id(listings),
        not_found={"v1|9|0": "could not fetch (HTTP 404)"},
    )
    reasons = {e["item_id"]: e["reason"] for e in stats["excluded"]}
    assert reasons == {
        "v1|2|0": "auction only: current bid is not a comparable",
        "v1|3|0": "postage unknown",
        "v1|4|0": "different currency (EUR)",
        "v1|9|0": "could not fetch (HTTP 404)",
    }
    assert stats["count"] == 1 and stats["currency"] == "GBP"
