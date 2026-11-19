"""Pure functions over :class:`~ebay_mcp.models.Listing` collections.

No network access here: the server fetches listings and hands them in. The MCP
only tidies data. Judging, ranking and advice are left to Claude, so this
module does just three mechanical jobs:

* :func:`apply_max_total` -- enforce a maximum *total* (item + postage) that
  eBay's own price filter can't express.
* :func:`filter_location` -- re-check the item-location restriction in code.
* :func:`comparable_statistics` -- p25 / median / max over the comparables
  Claude has chosen.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .models import Listing

MIN_SAMPLE = 5
PERCENTILE_METHOD = "linear interpolation between closest ranks (numpy default)"


def percentile(sorted_values: Sequence[float], q: float) -> float | None:
    """Linear-interpolation percentile (``q`` in ``[0, 100]``) over sorted data."""

    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (q / 100.0) * (len(sorted_values) - 1)
    low, high = math.floor(rank), math.ceil(rank)
    if low == high:
        return float(sorted_values[low])
    frac = rank - low
    return float(sorted_values[low] + (sorted_values[high] - sorted_values[low]) * frac)


# -- budget ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BudgetCheck:
    """Outcome of checking one listing against a maximum total.

    ``status`` is ``"within"`` (a verified total is within budget),
    ``"postage_unknown"`` (item price fits but the total can't be verified) or
    ``"over"`` (dropped).
    """

    status: str
    note: str | None = None


def check_budget(listing: Listing, max_total: float) -> BudgetCheck:
    paths: list[BudgetCheck] = []
    postage_known = listing.postage.known

    if listing.is_buy_it_now and listing.buy_it_now_price is not None:
        if postage_known:
            ok = listing.total_cost is not None and listing.total_cost <= max_total
            paths.append(BudgetCheck("within" if ok else "over"))
        elif listing.buy_it_now_price.value <= max_total:
            paths.append(BudgetCheck("postage_unknown"))

    if listing.is_auction and listing.current_bid is not None:
        if postage_known:
            total = listing.current_bid_total
            if total is not None and total <= max_total:
                note = (
                    "Buy It Now total is over budget; only the auction is within it."
                    if paths and paths[0].status == "over"
                    else None
                )
                paths.append(BudgetCheck("within", note))
            else:
                paths.append(BudgetCheck("over"))
        elif listing.current_bid.value <= max_total:
            paths.append(BudgetCheck("postage_unknown"))

    for wanted in ("within", "postage_unknown"):
        for path in paths:
            if path.status == wanted:
                return path
    return BudgetCheck("over")


def apply_max_total(
    listings: Iterable[Listing], max_total: float | None
) -> tuple[list[tuple[Listing, BudgetCheck | None]], int]:
    """Keep listings whose true total can fit ``max_total``.

    Returns ``(kept, dropped)``; each kept listing carries its
    :class:`BudgetCheck` (``None`` when no budget was given).
    """

    kept: list[tuple[Listing, BudgetCheck | None]] = []
    dropped = 0
    for listing in listings:
        if max_total is None:
            kept.append((listing, None))
            continue
        check = check_budget(listing, max_total)
        if check.status == "over":
            dropped += 1
        else:
            kept.append((listing, check))
    return kept, dropped


def filter_location(listings: Iterable[Listing], country: str | None) -> tuple[list[Listing], int]:
    """Drop listings located outside ``country``. Returns ``(kept, dropped)``."""

    items = list(listings)
    if not country:
        return items, 0
    kept = [i for i in items if (i.location_country or "").upper() in ("", country.upper())]
    return kept, len(items) - len(kept)


# -- comparables ---------------------------------------------------------------


def comparable_statistics(
    requested_ids: Sequence[str],
    listings: dict[str, Listing],
    *,
    not_found: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Price statistics over the comparables Claude selected.

    Only Buy It Now listings with a known postage figure count: an auction's
    current bid isn't a comparable price, and unknown postage would distort the
    total. Everything else is reported in ``excluded`` with a reason. With fewer
    than :data:`MIN_SAMPLE` comparables the percentile figures are withheld.
    """

    not_found = dict(not_found or {})
    included: list[tuple[str, float, str]] = []
    excluded: list[dict[str, str]] = []
    seen: set[str] = set()

    for item_id in requested_ids:
        if item_id in seen:
            continue
        seen.add(item_id)
        listing = listings.get(item_id)
        if listing is None:
            reason = not_found.get(item_id, "listing not found")
            excluded.append({"item_id": item_id, "reason": reason})
            continue
        if not listing.is_buy_it_now or listing.buy_it_now_price is None:
            excluded.append(
                {"item_id": item_id, "reason": "auction only: current bid is not a comparable"}
            )
            continue
        if not listing.postage.known or listing.total_cost is None:
            excluded.append({"item_id": item_id, "reason": "postage unknown"})
            continue
        included.append((item_id, listing.total_cost, listing.buy_it_now_price.currency))

    # Keep a single currency (the most common) so figures are unambiguous.
    currency = None
    if included:
        counts: dict[str, int] = {}
        for _, _, cur in included:
            counts[cur] = counts.get(cur, 0) + 1
        currency = max(counts, key=counts.__getitem__)
        for item_id, _, cur in [x for x in included if x[2] != currency]:
            excluded.append({"item_id": item_id, "reason": f"different currency ({cur})"})
        included = [x for x in included if x[2] == currency]

    included.sort(key=lambda x: x[1])
    totals = [t for _, t, _ in included]
    n = len(totals)
    result: dict[str, Any] = {
        "basis": "total cost = Buy It Now price + postage",
        "currency": currency,
        "count": n,
        "min_sample": MIN_SAMPLE,
        "low_confidence": n < MIN_SAMPLE,
        "prices": [{"item_id": i, "total_cost": t} for i, t, _ in included],
        "excluded": excluded,
    }
    if n >= MIN_SAMPLE:
        result.update(
            {
                "p25": round(percentile(totals, 25), 2),
                "median": round(statistics.median(totals), 2),
                "min": totals[0],
                "max": totals[-1],
                "percentile_method": PERCENTILE_METHOD,
            }
        )
    else:
        result["note"] = (
            f"Fewer than {MIN_SAMPLE} comparables: low confidence. No p25/median "
            "given; show these prices and give no 'what to pay' or bid figure."
        )
    return result
