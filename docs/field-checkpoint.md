# Field checkpoint (live calls, 2026-11-19)

Live calls against the production Browse API, marketplace `EBAY_GB`, with the
buyer's country and postcode set in the end-user context header. Made with
`uv run --env-file .env mcp-ebay search|item … --raw`. The raw responses are saved,
trimmed, in `tests/fixtures/` and are what the tests run against.

| Call | Query / item |
| --- | --- |
| search, 20 results | "nintendo switch oled" (Buy It Now mix) |
| search `buyingOptions:{AUCTION}` | "nintendo switch oled" (20 auctions) |
| search `fieldgroups=EXTENDED` | "nintendo switch oled" |
| search | "chesterfield sofa", "upright piano" (collection-only cases) |
| search `itemLocationCountry:US` | "vintage camera lens" (calculated postage) |
| search `itemLocationCountry:GB` / `CN` | "nintendo switch oled" |
| getItem | a Buy It Now listing, an auction-only listing, a collection-only sofa, a variation listing |

## Search summaries vs getItem

| Field | Search summary | getItem |
| --- | --- | --- |
| price | Buy It Now only. **Missing on auction-only listings** (16 of 20). Auction + BIN: `price` = the BIN price | Present. **Auction-only: `price` equals the current bid**, so it is not a BIN price |
| currentBidPrice, bidCount, itemEndDate | Yes, on every auction | Yes |
| minimumPriceToBid, uniqueBidderCount | No | Yes (auctions) |
| shippingOptions (cost, FIXED/CALCULATED) | Yes, priced to the configured postcode | Yes, plus carrier/service |
| Collection only | `shippingOptions` **absent** | `shippingOptions` absent; `estimatedAvailabilities.deliveryOptions = [SELLER_ARRANGED_LOCAL_PICKUP]` |
| seller username, feedback %, feedback score, account type | Yes | Yes |
| topRatedBuyingExperience | Yes | Yes |
| itemLocation country + partial postcode | Yes (city only with EXTENDED) | Yes, with city |
| condition, conditionId | Yes | Yes |
| conditionDescription (seller's condition note) | No | When the seller wrote one |
| Item specifics (`localizedAspects`) | **No**, not even with `fieldgroups=EXTENDED` | Yes |
| returnTerms | No | Yes |
| description (HTML) | No (`shortDescription` only with EXTENDED) | Yes |
| lotSize | No | Yes |
| estimated available / sold quantity | No | Yes |
| itemGroupHref / itemGroupType (variations) | Yes | getItem works on the variation id |

## Filters

- `itemLocationCountry:GB` works: 2,793 matches vs 3,402 without it; all results located in GB.
  Without it, results are mostly but not only GB, so the filter is sent and re-checked in code.
- `price:[..X]` caps the **item price** for Buy It Now and the **current bid** for auctions
  (an auction at £59.36 with a £153.59 BIN passed a £80 cap). Postage is ignored: 5 of 50
  Buy It Now results under £100 came to over £100 with postage. The server therefore sends
  `max_total` as a loose cap and re-checks the true total.
- `conditionIds:{…}` works for the condition groups (`used` returned "Used" and "Very Good").
- `buyingOptions:{FIXED_PRICE}` / `{AUCTION}` work.

## Postage

- Every result from the console searches had FIXED postage with a cost.
- Collection-only items have no `shippingOptions` at all (7 of 50 sofas, 22 of 50 pianos).
  These are reported as `postage.status = "unknown"`, never 0.
- CALCULATED postage (seen on US-located items) still came back with a cost to the configured
  postcode, converted to GBP. It is treated as known and labelled `type: CALCULATED`.

## Design changes made because of this

1. Auction fields come from search directly; details are only needed for minimum bid and
   bidder count.
2. `price` is only used as a Buy It Now price when `FIXED_PRICE` is a buying option
   (getItem reports the current bid as `price` for auction-only listings).
3. Item specifics, return terms, condition notes, lot size and delivery options come from
   `get_listing_details` only. Part A says so.
4. Location filter: the eBay filter plus a re-check in code.
5. `max_total`: loose eBay price cap, then a true-total check in code. Auctions are checked on
   current bid + postage; unknown-postage listings are kept and marked `postage_unknown`.
6. `price_statistics` excludes auction-only listings and unknown-postage listings, names the
   reason, and withholds percentiles below 5 comparables.
