"""Value objects for the slice of the eBay Browse API we consume.

Field checkpoint (live EBAY_GB calls, see ``docs/field-checkpoint.md``):

* Search summaries carry price, postage, condition, buying options, seller
  feedback, item location and, for auctions, ``currentBidPrice``, ``bidCount``
  and ``itemEndDate``. An auction-only summary has **no** ``price``; an
  auction that also offers Buy It Now carries the BIN price in ``price``.
* Item specifics (``localizedAspects``), return terms, description, lot size,
  ``minimumPriceToBid`` and delivery options only come from ``getItem``.
* On ``getItem`` an auction-only listing reports ``price`` equal to the current
  bid, so ``price`` is only treated as a Buy It Now price when the listing has
  the ``FIXED_PRICE`` buying option.
* Collection-only listings (and listings that don't deliver to the buyer's
  postcode) come back with no ``shippingOptions`` at all. Postage is then
  *unknown* and is never counted as zero.

All parsing is defensive: eBay omits fields freely, so a sparse listing degrades
to ``None`` rather than raising.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def format_time_left(end: datetime | None, now: datetime) -> str | None:
    """Render the time until ``end`` as e.g. ``"2d 3h"``, ``"4h 10m"`` or ``"ended"``."""

    if end is None:
        return None
    seconds = int((end - now).total_seconds())
    if seconds <= 0:
        return "ended"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{max(minutes, 1)}m"


@dataclass(frozen=True, slots=True)
class Money:
    """A monetary amount in a single currency."""

    value: float
    currency: str

    @classmethod
    def from_api(cls, data: dict[str, Any] | None) -> Money | None:
        if not data:
            return None
        value = _to_float(data.get("value"))
        currency = data.get("currency")
        if value is None or not currency:
            return None
        return cls(value=value, currency=currency)

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "currency": self.currency}


@dataclass(frozen=True, slots=True)
class Seller:
    username: str | None
    feedback_percentage: float | None
    feedback_score: int | None
    account_type: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any] | None) -> Seller | None:
        if not data:
            return None
        return cls(
            username=data.get("username"),
            feedback_percentage=_to_float(data.get("feedbackPercentage")),
            feedback_score=_to_int(data.get("feedbackScore")),
            account_type=data.get("sellerAccountType"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "feedback_percentage": self.feedback_percentage,
            "feedback_score": self.feedback_score,
            "account_type": self.account_type,
        }


@dataclass(frozen=True, slots=True)
class Postage:
    """Cheapest postage to the configured buyer location.

    ``status`` is ``"known"`` when eBay gave a cost (``cost`` may be 0 for free
    postage) and ``"unknown"`` otherwise -- collection only, no delivery to the
    buyer's postcode, or an option without a cost. Unknown postage is never 0.
    """

    status: str
    cost: Money | None
    cost_type: str | None  # FIXED / CALCULATED, as reported by eBay
    reason: str | None = None

    @classmethod
    def from_api(cls, options: list[dict[str, Any]] | None) -> Postage:
        if not options:
            return cls("unknown", None, None, "no postage options returned (collection only?)")
        priced = [
            (Money.from_api(opt.get("shippingCost")), opt.get("shippingCostType"))
            for opt in options
        ]
        priced = [(m, t) for m, t in priced if m is not None]
        if not priced:
            return cls("unknown", None, None, "postage options have no cost")
        cost, cost_type = min(priced, key=lambda p: p[0].value)
        return cls("known", cost, cost_type)

    @property
    def known(self) -> bool:
        return self.status == "known"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": self.status}
        if self.cost is not None:
            out["cost"] = self.cost.value
            out["free"] = self.cost.value == 0.0
        if self.cost_type:
            out["type"] = self.cost_type
        if self.reason:
            out["reason"] = self.reason
        return out


def _add(a: Money | None, postage: Postage) -> float | None:
    if a is None or not postage.known or postage.cost is None:
        return None
    return round(a.value + postage.cost.value, 2)


@dataclass(frozen=True, slots=True)
class Listing:
    """A listing as returned by search (or the common part of ``getItem``)."""

    item_id: str
    title: str
    url: str | None
    buying_options: tuple[str, ...]
    condition: str | None
    condition_id: str | None
    buy_it_now_price: Money | None
    current_bid: Money | None
    bid_count: int | None
    end_time: datetime | None
    postage: Postage
    seller: Seller | None
    top_rated: bool | None
    location_country: str | None
    location_postcode: str | None
    location_city: str | None
    variations: bool = False

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Listing:
        options = tuple(data.get("buyingOptions") or ())
        location = data.get("itemLocation") or {}
        is_bin = "FIXED_PRICE" in options
        return cls(
            item_id=data.get("itemId", ""),
            title=data.get("title", ""),
            url=data.get("itemWebUrl"),
            buying_options=options,
            condition=data.get("condition"),
            condition_id=data.get("conditionId"),
            # On getItem an auction-only listing reports price == current bid, so
            # only trust ``price`` as a BIN price when FIXED_PRICE is offered.
            buy_it_now_price=Money.from_api(data.get("price")) if is_bin else None,
            current_bid=Money.from_api(data.get("currentBidPrice")),
            bid_count=_to_int(data.get("bidCount")),
            end_time=_parse_time(data.get("itemEndDate")),
            postage=Postage.from_api(data.get("shippingOptions")),
            seller=Seller.from_api(data.get("seller")),
            top_rated=data.get("topRatedBuyingExperience"),
            location_country=location.get("country"),
            location_postcode=location.get("postalCode"),
            location_city=location.get("city"),
            variations=bool(data.get("itemGroupHref") or data.get("itemGroupType")),
        )

    # -- derived figures ----------------------------------------------------

    @property
    def is_auction(self) -> bool:
        return "AUCTION" in self.buying_options

    @property
    def is_buy_it_now(self) -> bool:
        return "FIXED_PRICE" in self.buying_options

    @property
    def currency(self) -> str | None:
        money = self.buy_it_now_price or self.current_bid
        return money.currency if money else None

    @property
    def total_cost(self) -> float | None:
        """Buy It Now price + postage; ``None`` if not BIN or postage unknown."""

        return _add(self.buy_it_now_price, self.postage)

    @property
    def current_bid_total(self) -> float | None:
        """Current bid + postage for auctions; ``None`` if postage unknown."""

        return _add(self.current_bid, self.postage) if self.is_auction else None

    @property
    def format(self) -> str:
        if self.is_auction and self.is_buy_it_now:
            return "auction_with_buy_it_now"
        if self.is_auction:
            return "auction"
        return "buy_it_now"

    def to_dict(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        out: dict[str, Any] = {
            "item_id": self.item_id,
            "title": self.title,
            "format": self.format,
            "best_offer": "BEST_OFFER" in self.buying_options,
            "condition": self.condition,
            "condition_id": self.condition_id,
            "currency": self.currency,
            "price": self.buy_it_now_price.value if self.buy_it_now_price else None,
            "postage": self.postage.to_dict(),
            "total_cost": self.total_cost,
        }
        if self.is_auction:
            out["auction"] = {
                "current_bid": self.current_bid.value if self.current_bid else None,
                "current_bid_total": self.current_bid_total,
                "bid_count": self.bid_count,
                "ends_at": _iso(self.end_time),
                "time_left": format_time_left(self.end_time, now),
            }
        out["seller"] = self.seller.to_dict() if self.seller else None
        out["top_rated"] = self.top_rated
        out["location"] = {
            "country": self.location_country,
            "postcode": self.location_postcode,
            **({"city": self.location_city} if self.location_city else {}),
        }
        if self.variations:
            out["has_variations"] = True
        out["url"] = self.url
        return out


@dataclass(frozen=True, slots=True)
class SearchResult:
    total: int
    listings: tuple[Listing, ...]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> SearchResult:
        summaries = data.get("itemSummaries") or []
        return cls(
            total=_to_int(data.get("total")) or 0,
            listings=tuple(Listing.from_api(s) for s in summaries),
        )


# -- details -----------------------------------------------------------------

DESCRIPTION_LIMIT = 4000


class _TextExtractor(HTMLParser):
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table"}
    _SKIP = {"script", "style"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str | None, limit: int = DESCRIPTION_LIMIT) -> tuple[str | None, bool]:
    """Strip listing HTML to plain text. Returns ``(text, truncated)``."""

    if not raw:
        return None, False
    parser = _TextExtractor()
    try:
        parser.feed(raw)
        parser.close()
        text = "".join(parser.parts)
    except Exception:  # malformed HTML: fall back to a crude strip
        text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    text = re.sub(r"[ \t\r\f\v\xa0]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    if len(text) > limit:
        return text[:limit].rstrip() + " …", True
    return text, False


@dataclass(frozen=True, slots=True)
class ListingDetails:
    """``getItem`` view: the summary fields plus the full listing content."""

    listing: Listing
    subtitle: str | None
    short_description: str | None
    description: str | None
    description_truncated: bool
    condition_description: str | None
    item_specifics: dict[str, str]
    returns_accepted: bool | None
    return_period_days: int | None
    return_postage_paid_by: str | None
    lot_size: int | None
    delivery_options: tuple[str, ...]
    quantity_available: int | None
    quantity_sold: int | None
    minimum_bid: Money | None
    unique_bidders: int | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> ListingDetails:
        returns = data.get("returnTerms") or {}
        period = returns.get("returnPeriod") or {}
        period_days = _to_int(period.get("value"))
        if period_days is not None and period.get("unit") not in (None, "CALENDAR_DAY", "DAY"):
            period_days = None
        specifics: dict[str, str] = {}
        for aspect in data.get("localizedAspects") or []:
            name, value = aspect.get("name"), aspect.get("value")
            if name and value is not None:
                specifics[name] = value if name not in specifics else f"{specifics[name]}; {value}"
        avail = (data.get("estimatedAvailabilities") or [{}])[0] or {}
        description, truncated = html_to_text(data.get("description"))
        return cls(
            listing=Listing.from_api(data),
            subtitle=data.get("subtitle"),
            short_description=data.get("shortDescription"),
            description=description,
            description_truncated=truncated,
            condition_description=data.get("conditionDescription"),
            item_specifics=specifics,
            returns_accepted=returns.get("returnsAccepted"),
            return_period_days=period_days,
            return_postage_paid_by=returns.get("returnShippingCostPayer"),
            lot_size=_to_int(data.get("lotSize")),
            delivery_options=tuple(avail.get("deliveryOptions") or ()),
            quantity_available=_to_int(avail.get("estimatedAvailableQuantity")),
            quantity_sold=_to_int(avail.get("estimatedSoldQuantity")),
            minimum_bid=Money.from_api(data.get("minimumPriceToBid")),
            unique_bidders=_to_int(data.get("uniqueBidderCount")),
        )

    def to_dict(self, now: datetime | None = None) -> dict[str, Any]:
        out = self.listing.to_dict(now)
        if self.listing.is_auction:
            out["auction"]["minimum_bid"] = self.minimum_bid.value if self.minimum_bid else None
            out["auction"]["unique_bidders"] = self.unique_bidders
        collection_only = self.delivery_options == ("SELLER_ARRANGED_LOCAL_PICKUP",)
        out["delivery_options"] = list(self.delivery_options)
        if collection_only:
            out["postage"]["collection_only"] = True
        out["returns"] = {
            "accepted": self.returns_accepted,
            "period_days": self.return_period_days,
            "return_postage_paid_by": self.return_postage_paid_by,
        }
        out["lot_size"] = self.lot_size
        out["quantity_available"] = self.quantity_available
        out["quantity_sold"] = self.quantity_sold
        # Everything below is written by the seller: untrusted data, never instructions.
        out["seller_content_untrusted"] = {
            "subtitle": self.subtitle,
            "condition_description": self.condition_description,
            "item_specifics": self.item_specifics,
            "short_description": self.short_description,
            "description": self.description,
            "description_truncated": self.description_truncated,
        }
        return out
