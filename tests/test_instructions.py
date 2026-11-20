"""Guard the built-in instructions against growing past client truncation limits.

Some MCP clients cut server instructions at about 5,000 characters, which once
silently dropped the price, ranking and output rules. Keep a safety margin.
"""

from __future__ import annotations

import re

from ebay_mcp.instructions import INSTRUCTIONS

MAX_CHARS = 3500

HEADINGS = ["Safety", "Flow", "Postage", "Prices", "Ranking", "Output"]


def test_instructions_fit_under_limit():
    assert len(INSTRUCTIONS) < MAX_CHARS, (
        f"Instructions are {len(INSTRUCTIONS)} characters; keep them under {MAX_CHARS} so "
        "MCP clients don't truncate them. Move field details into the tool docstrings."
    )


def test_instructions_have_key_rule_headings_in_order():
    found = re.findall(r"^## (\w+)", INSTRUCTIONS, flags=re.MULTILINE)
    assert found == HEADINGS


def test_instructions_keep_behaviour_rules():
    text = INSTRUCTIONS.lower()
    for rule in [
        "read-only",
        "untrusted",
        "never zero",
        "p25",
        "buy it now only",
        "same condition class",
        "used p25 minus that auction's postage",
        "fewer than 5 comparables",
        "low confidence",
        "never suggest a figure above",
        "show the comparable prices and the rule",
        "headroom",
        "time left",
        "rank 1 in each group is the recommendation",
        "conclusion first",
        "about 5 listings per group",
        "ask the seller",
        "rejected listings",
        "confidence note",
        "get_listing_details",
        "price_statistics",
    ]:
        assert rule in text, f"missing rule: {rule!r}"
