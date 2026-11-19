# mcp-ebay

A read-only [MCP](https://modelcontextprotocol.io) server that lets Claude hunt for
deals on eBay (eBay UK by default). The server only fetches and tidies listing data.
Claude does the judging, ranking and advice, following the built-in instructions the
server ships with (Part A of [`docs/brief.md`](docs/brief.md)).

There are no tools that bid, buy or touch an eBay account. It uses the public Buy Browse
API with an application token only.

## Tools

| Tool | What it returns |
| --- | --- |
| `search_listings` | Lightweight summaries: price, postage (known/unknown), total cost, condition, format, seller feedback, location and, for auctions, current bid, bid count, end time and time left. Plus eBay's total match count. Optional `max_total` (item + postage), `conditions`, `buying_format`, `sort`, `limit` (≤200). |
| `get_listing_details` | One listing in full: description (as text), item specifics, condition notes, return terms, lot size, delivery options, auction minimum bid. Seller-written text is grouped under `seller_content_untrusted`. |
| `price_statistics` | p25 / median / min / max of total cost over item IDs Claude chose as comparables. Buy It Now only, known postage only. Withholds figures below 5 comparables. |

Defaults are set once in the environment: items located in the marketplace's country,
and postage calculated to your postcode.

What eBay returns where (search vs getItem, auctions, collection-only postage) is in
[`docs/field-checkpoint.md`](docs/field-checkpoint.md).

## Setup

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/) and an eBay developer app
(<https://developer.ebay.com/my/keys>: the App ID and Cert ID).

```bash
uv sync --extra dev
cp .env.example .env   # then fill in the keys and your postcode
```

| Variable | Default | Description |
| --- | --- | --- |
| `EBAY_CLIENT_ID` | none | App ID (Client ID). **Required.** |
| `EBAY_CLIENT_SECRET` | none | Cert ID (Client Secret). **Required.** |
| `EBAY_ENVIRONMENT` | `production` | `production` or `sandbox`. |
| `EBAY_MARKETPLACE_ID` | `EBAY_GB` | Marketplace to search. |
| `EBAY_ITEM_LOCATION_COUNTRY` | marketplace country | Only items located here; `ANY` disables. |
| `EBAY_DELIVERY_COUNTRY` | none | Buyer country for postage. |
| `EBAY_DELIVERY_POSTAL_CODE` | none | Buyer postcode for postage (private). |
| `MCP_AUTH_TOKEN` | none | Bearer token for the HTTP transport (≥32 chars). |
| `EBAY_TIMEOUT` / `EBAY_MAX_RETRIES` | `20` / `3` | Network tuning. |

Keys live only in the environment (or a git-ignored `.env`).

## Run

Check credentials, then try the tools from a terminal. The non-raw commands call the
real MCP tools, so you see exactly what Claude sees:

```bash
uv run --env-file .env mcp-ebay check
uv run --env-file .env mcp-ebay search "nintendo switch oled" --max-total 150 --condition used
uv run --env-file .env mcp-ebay search "nintendo switch oled" --format auction --sort ending_soonest
uv run --env-file .env mcp-ebay item 'v1|123456789012|0'
uv run --env-file .env mcp-ebay stats 'v1|…|0' 'v1|…|0' 'v1|…|0' 'v1|…|0' 'v1|…|0'
uv run --env-file .env mcp-ebay search "…" --raw   # eBay's unprocessed JSON
```

### Local (stdio) with Claude Code

```bash
claude mcp add ebay -- uv run --directory "$PWD" --env-file "$PWD/.env" mcp-ebay serve
```

Or use the bundled `.mcp.json`, which reads the variables from your shell environment.

### HTTP

```bash
export MCP_AUTH_TOKEN=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
uv run --env-file .env mcp-ebay serve --transport http --host 127.0.0.1 --port 8000
```

The endpoint is `http://127.0.0.1:8000/mcp` (streamable HTTP). Every request needs
`Authorization: Bearer $MCP_AUTH_TOKEN`; without it the server answers 401. The server
refuses to start over HTTP if `MCP_AUTH_TOKEN` is unset or shorter than 32 characters.
`--port` defaults to `$PORT` when set.

## Test

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Tests never call eBay. They run the client, the parsing and the MCP tools (in memory
and over the HTTP app) against real responses saved in `tests/fixtures/`. To refresh
the fixtures, re-run the `--raw` commands above and save their output there.

## Deploy to FastMCP Cloud

Not deployed yet. To do it:

1. Push this repository to GitHub.
2. In [FastMCP Cloud](https://fastmcp.cloud), create a project from the repo with the
   entrypoint `src/ebay_mcp/cloud.py:mcp`. Dependencies install from `pyproject.toml`.
3. Set environment variables in the project: `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`,
   `EBAY_MARKETPLACE_ID`, `EBAY_DELIVERY_COUNTRY`, `EBAY_DELIVERY_POSTAL_CODE` and
   `MCP_AUTH_TOKEN`.
4. Leave the platform's own authentication off. The server enforces the bearer token
   itself, and `cloud.py` fails at import if `MCP_AUTH_TOKEN` is missing, so it can't
   come up open.
5. Connect from Claude with the deployment's `/mcp` URL and the header
   `Authorization: Bearer <MCP_AUTH_TOKEN>`.

To check that the entrypoint loads the way the platform loads it:

```bash
MCP_AUTH_TOKEN=… uv run fastmcp inspect src/ebay_mcp/cloud.py:mcp
```

## Layout

- `config.py`: environment configuration.
- `auth.py`: eBay OAuth client-credentials token cache.
- `client.py`: the only module that calls eBay (filters, retries).
- `models.py`: listing summary/details parsing (postage, auction fields).
- `analysis.py`: pure functions: true-total budget check, location re-check, comparables statistics.
- `instructions.py`: Part A, served as the MCP instructions.
- `server.py`: the three tools. `http_auth.py`: bearer-token verifier. `cloud.py`: FastMCP Cloud entrypoint.
- `cli.py`: the `mcp-ebay` command.

## Limits

- Prices are current asking prices. Sold prices need eBay's restricted Marketplace
  Insights API, which isn't used.
- The Browse API allows about 5,000 calls a day. Each search or details call is one call.
  `price_statistics` reuses listings already fetched in the same server process and only
  calls eBay for IDs it hasn't seen.

Based on [luke-nielsen/ebay-mcp](https://github.com/luke-nielsen/ebay-mcp). MIT licence.
