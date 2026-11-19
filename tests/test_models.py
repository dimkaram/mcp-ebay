"""Parsing of real eBay responses (saved fixtures) into Listing / ListingDetails."""

from __future__ import annotations

from datetime import UTC, datetime

from conftest import load, summary

from ebay_mcp.models import (
    Listing,
    ListingDetails,
    Money,
    Postage,
    SearchResult,
    format_time_left,
    html_to_text,
)

NOW = datetime(2026, 11, 19, 2, 0, tzinfo=UTC)


def test_search_result_counts():
    result = SearchResult.from_api(load("search_fixed_raw"))
    assert result.total == 3401
    assert len(result.listings) == 20


def test_fixed_price_summary():
    listing = Listing.from_api(summary("search_fixed_raw", "v1|920157680853|0"))
    assert listing.format == "buy_it_now"
    assert listing.buy_it_now_price == Money(99.99, "GBP")
    assert listing.postage.known and listing.postage.cost == Money(3.38, "GBP")
    assert listing.total_cost == 103.37
    assert listing.current_bid is None and listing.current_bid_total is None
    assert listing.seller.feedback_percentage == 100.0
    assert listing.seller.feedback_score == 911
    assert listing.seller.account_type == "INDIVIDUAL"
    assert listing.location_country == "GB"
    out = listing.to_dict(NOW)
    assert "auction" not in out
    assert out["postage"] == {"status": "known", "cost": 3.38, "free": False, "type": "FIXED"}


def test_auction_only_summary_has_no_buy_it_now_price():
    listing = Listing.from_api(summary("search_auction_raw", "v1|168858079790|0"))
    assert listing.format == "auction"
    assert listing.buy_it_now_price is None
    assert listing.total_cost is None
    assert listing.current_bid == Money(102.08, "GBP")
    assert listing.current_bid_total == 105.97
    assert listing.bid_count == 9
    auction = listing.to_dict(NOW)["auction"]
    assert auction["ends_at"] == "2026-11-19T22:10:54Z"
    assert auction["time_left"] == "20h 10m"


def test_auction_with_buy_it_now_keeps_both_prices():
    listing = Listing.from_api(summary("search_auction_raw", "v1|880114981035|0"))
    assert listing.format == "auction_with_buy_it_now"
    assert listing.buy_it_now_price == Money(89.99, "GBP")
    assert listing.current_bid == Money(59.99, "GBP")
    assert listing.total_cost == 93.64
    assert listing.current_bid_total == 63.64


def test_collection_only_postage_is_unknown_not_zero():
    listing = Listing.from_api(summary("search_sofa_raw", "v1|940096609988|0"))
    assert listing.postage.status == "unknown"
    assert listing.postage.cost is None
    assert listing.total_cost is None
    out = listing.to_dict(NOW)
    assert out["total_cost"] is None
    assert "cost" not in out["postage"]


def test_variation_listing_flagged():
    listing = Listing.from_api(summary("search_fixed_raw", "v1|364710736005|634243887933"))
    assert listing.variations is True
    assert listing.to_dict(NOW)["has_variations"] is True


def test_postage_edge_cases():
    assert Postage.from_api([{"shippingCostType": "CALCULATED"}]).status == "unknown"
    calc = Postage.from_api(
        [{"shippingCostType": "CALCULATED", "shippingCost": {"value": "13.96", "currency": "GBP"}}]
    )
    assert calc.known and calc.cost_type == "CALCULATED"
    cheapest = Postage.from_api(
        [
            {"shippingCost": {"value": "9.99", "currency": "GBP"}},
            {"shippingCost": {"value": "0.00", "currency": "GBP"}},
        ]
    )
    assert cheapest.cost.value == 0.0 and cheapest.to_dict()["free"] is True


def test_get_item_auction_price_is_not_treated_as_buy_it_now():
    # getItem reports price == current bid for an auction-only listing.
    details = ListingDetails.from_api(load("item_auction_raw"))
    assert details.listing.buy_it_now_price is None
    assert details.listing.current_bid == Money(102.08, "GBP")
    assert details.minimum_bid == Money(104.16, "GBP")
    assert details.unique_bidders == 5
    out = details.to_dict(NOW)
    assert out["price"] is None
    assert out["auction"]["minimum_bid"] == 104.16


def test_get_item_fixed_details():
    details = ListingDetails.from_api(load("item_fixed_raw"))
    assert details.returns_accepted is False
    assert details.lot_size == 0
    assert details.item_specifics["Platform"] == "Nintendo Switch"
    assert len(details.item_specifics) >= 10
    assert details.description and "<" not in details.description
    out = details.to_dict(NOW)
    untrusted = out["seller_content_untrusted"]
    assert untrusted["item_specifics"]["Platform"] == "Nintendo Switch"
    assert untrusted["description"] == details.description
    assert out["returns"]["accepted"] is False


def test_get_item_collection_only():
    details = ListingDetails.from_api(load("item_collection_raw"))
    assert details.delivery_options == ("SELLER_ARRANGED_LOCAL_PICKUP",)
    assert details.return_period_days == 30
    assert details.return_postage_paid_by == "BUYER"
    out = details.to_dict(NOW)
    assert out["postage"]["status"] == "unknown"
    assert out["postage"]["collection_only"] is True


def test_html_to_text():
    text, truncated = html_to_text(
        "<div><p>Hello&nbsp;<b>world</b></p><script>evil()</script><ul><li>a</li><li>b</li></ul>"
    )
    assert text == "Hello world\na\nb"
    assert truncated is False
    long_text, truncated = html_to_text("<p>" + "x" * 50 + "</p>", limit=10)
    assert truncated and long_text.startswith("x" * 10)
    assert html_to_text(None) == (None, False)


def test_format_time_left():
    assert format_time_left(datetime(2026, 11, 21, 5, 0, tzinfo=UTC), NOW) == "2d 3h"
    assert format_time_left(datetime(2026, 11, 19, 2, 0, 30, tzinfo=UTC), NOW) == "1m"
    assert format_time_left(datetime(2026, 11, 19, 1, 0, tzinfo=UTC), NOW) == "ended"
    assert format_time_left(None, NOW) is None


def test_money_rejects_incomplete_data():
    assert Money.from_api(None) is None
    assert Money.from_api({"value": "5.00"}) is None
    assert Money.from_api({"value": "x", "currency": "GBP"}) is None
