"""Async HTTP client for the eBay Buy Browse API.

This is the only module that talks to the network. It owns an
:class:`httpx.AsyncClient`, attaches the OAuth token and marketplace headers,
translates friendly Python arguments into eBay's filter/sort grammar, retries
transient failures with exponential backoff, and returns typed
:class:`~ebay_mcp.models` objects.

Usage::

    async with EbayClient(Config.load()) as client:
        result = await client.search("nintendo switch oled", limit=50)
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import httpx

from .auth import TokenManager
from .config import Config
from .errors import EbayAPIError, EbayError
from .models import Listing, ListingDetails, SearchResult

# Map friendly sort keys to the Browse API's sort grammar.
_SORT_MAP = {
    "best_match": None,  # eBay's default relevance ranking; omit the parameter.
    "price_asc": "price",
    "price_desc": "-price",
    "newly_listed": "newlyListed",
    "ending_soonest": "endingSoonest",
}

# Friendly condition groups -> eBay condition ids (``conditionIds`` filter).
CONDITION_GROUPS: dict[str, tuple[str, ...]] = {
    "new": ("1000",),
    "new_other": ("1500", "1750"),  # open box / new other, new with defects
    "refurbished": ("2000", "2010", "2020", "2030", "2500"),
    "used": ("2750", "3000", "4000", "5000", "6000"),  # like new .. acceptable
    "for_parts": ("7000",),
}

# Friendly buying formats -> eBay ``buyingOptions`` values.
BUYING_FORMATS: dict[str, tuple[str, ...]] = {
    "any": (),
    "buy_it_now": ("FIXED_PRICE",),
    "auction": ("AUCTION",),
}

# eBay caps a single Browse search page at 200 items.
MAX_PAGE_LIMIT = 200


class EbayClient:
    """A thin, typed async wrapper over the eBay Browse API."""

    def __init__(
        self,
        config: Config,
        *,
        http: httpx.AsyncClient | None = None,
        token_manager: TokenManager | None = None,
    ) -> None:
        self._config = config
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient()
        self._tokens = token_manager or TokenManager(config, self._http)

    async def __aenter__(self) -> EbayClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # -- public API ---------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        limit: int = 50,
        sort: str = "best_match",
        conditions: list[str] | None = None,
        buying_format: str = "any",
        max_item_price: float | None = None,
        location_country: str | None = None,
    ) -> SearchResult:
        """Search active listings via ``item_summary/search``.

        ``max_item_price`` is sent as eBay's price filter, which caps the item
        price (current bid for auctions) and ignores postage: callers must check
        the true total themselves.
        """

        if not query or not query.strip():
            raise EbayError("Search query must be a non-empty string.")
        if sort not in _SORT_MAP:
            raise EbayError(f"Unknown sort {sort!r}; expected one of {sorted(_SORT_MAP)}.")

        params: dict[str, Any] = {
            "q": query.strip(),
            "limit": max(1, min(int(limit), MAX_PAGE_LIMIT)),
        }
        sort_value = _SORT_MAP[sort]
        if sort_value:
            params["sort"] = sort_value
        filters = self.build_filters(
            conditions=conditions,
            buying_format=buying_format,
            max_item_price=max_item_price,
            location_country=location_country,
        )
        if filters:
            params["filter"] = filters

        data = await self._get("/item_summary/search", params=params)
        return SearchResult.from_api(data)

    async def get_item(self, item_id: str) -> ListingDetails:
        """Fetch the full listing (description, specifics, returns) by item id."""

        return ListingDetails.from_api(await self._get(_item_path(item_id)))

    async def get_listing(self, item_id: str) -> Listing:
        """Fetch one listing and keep only the summary-level fields."""

        return Listing.from_api(await self._get(_item_path(item_id)))

    # -- filter / header construction --------------------------------------

    def build_filters(
        self,
        *,
        conditions: list[str] | None = None,
        buying_format: str = "any",
        max_item_price: float | None = None,
        location_country: str | None = None,
    ) -> str:
        """Assemble eBay's comma-separated ``filter`` query value."""

        clauses: list[str] = []

        if max_item_price is not None:
            if max_item_price <= 0:
                raise EbayError("max_total must be greater than zero.")
            clauses.append(f"price:[..{float(max_item_price):g}]")
            # priceCurrency is required by eBay whenever a price filter is set.
            clauses.append(f"priceCurrency:{_marketplace_currency(self._config.marketplace_id)}")

        if conditions:
            ids: list[str] = []
            for key in conditions:
                group = CONDITION_GROUPS.get(key.strip().lower())
                if group is None:
                    raise EbayError(
                        f"Unknown condition {key!r}; expected any of {sorted(CONDITION_GROUPS)}."
                    )
                ids.extend(i for i in group if i not in ids)
            clauses.append("conditionIds:{" + "|".join(ids) + "}")

        fmt = (buying_format or "any").strip().lower()
        if fmt not in BUYING_FORMATS:
            raise EbayError(
                f"Unknown buying_format {buying_format!r}; "
                f"expected one of {sorted(BUYING_FORMATS)}."
            )
        if BUYING_FORMATS[fmt]:
            clauses.append("buyingOptions:{" + "|".join(BUYING_FORMATS[fmt]) + "}")

        if location_country:
            clauses.append(f"itemLocationCountry:{location_country.upper()}")

        return ",".join(clauses)

    def _headers(self, token: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": self._config.marketplace_id,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        ctx = _delivery_context(self._config)
        if ctx:
            headers["X-EBAY-C-ENDUSERCTX"] = ctx
        return headers

    # -- request plumbing ---------------------------------------------------

    async def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._config.browse_base_url}{path}"
        last_exc: Exception | None = None

        for attempt in range(self._config.max_retries):
            token = await self._tokens.get_token(force_refresh=attempt > 0 and _was_auth(last_exc))
            try:
                response = await self._http.get(
                    url,
                    params=params,
                    headers=self._headers(token),
                    timeout=self._config.timeout,
                )
            except httpx.HTTPError as exc:
                last_exc = exc
                await self._backoff(attempt)
                continue

            if response.status_code == 200:
                return response.json()

            # 401 once -> force a token refresh and retry; repeated 401 is fatal.
            if response.status_code == 401 and attempt + 1 < self._config.max_retries:
                last_exc = _api_error(response)
                continue

            if response.status_code == 429 or response.status_code >= 500:
                last_exc = _api_error(response)
                if attempt + 1 < self._config.max_retries:
                    await self._backoff(attempt, retry_after=response.headers.get("Retry-After"))
                    continue

            raise _api_error(response)

        # Exhausted retries.
        if isinstance(last_exc, EbayError):
            raise last_exc
        raise EbayError(
            f"Request to {path} failed after {self._config.max_retries} attempts: {last_exc}"
        )

    async def _backoff(self, attempt: int, *, retry_after: str | None = None) -> None:
        if retry_after and retry_after.isdigit():
            delay = float(retry_after)
        else:
            delay = min(2.0**attempt, 8.0)
        await asyncio.sleep(delay)


def _was_auth(exc: Exception | None) -> bool:
    return isinstance(exc, EbayAPIError) and exc.status_code == 401


def _api_error(response: httpx.Response) -> EbayAPIError:
    """Build an :class:`EbayAPIError` from an error response body."""

    errors: list = []
    message = f"eBay API returned HTTP {response.status_code}"
    try:
        payload = response.json()
        errors = payload.get("errors") or []
        if errors and isinstance(errors, list):
            first = errors[0]
            message = first.get("message") or first.get("longMessage") or message
    except (ValueError, AttributeError):
        body = (response.text or "").strip()
        if body:
            message = f"{message}: {body[:300]}"
    return EbayAPIError(response.status_code, message, errors=errors)


def _item_path(item_id: str) -> str:
    if not item_id or not item_id.strip():
        raise EbayError("item_id must be a non-empty string.")
    return "/item/" + quote(item_id.strip(), safe="|")


# Default transactional currency per marketplace; used for the priceCurrency
# filter. Marketplaces not listed fall back to USD, which eBay tolerates for
# US-based searches.
_MARKETPLACE_CURRENCY = {
    "EBAY_US": "USD",
    "EBAY_GB": "GBP",
    "EBAY_DE": "EUR",
    "EBAY_FR": "EUR",
    "EBAY_IT": "EUR",
    "EBAY_ES": "EUR",
    "EBAY_AU": "AUD",
    "EBAY_CA": "CAD",
}


def _marketplace_currency(marketplace_id: str) -> str:
    return _MARKETPLACE_CURRENCY.get(marketplace_id, "USD")


def _delivery_context(config: Config) -> str | None:
    """Build the X-EBAY-C-ENDUSERCTX header for delivery-aware pricing."""

    if not config.delivery_country:
        return None
    location = f"country={config.delivery_country}"
    if config.delivery_postal_code:
        location += f",zip={config.delivery_postal_code}"
    # The header value is itself URL-encoded (postcodes may contain spaces).
    return "contextualLocation=" + quote(location, safe="")
