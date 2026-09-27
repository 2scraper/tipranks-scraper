#!/usr/bin/env python3
"""tipranks_parser.py — the only file in this repo carrying tipranks.com
knowledge. Every fact below is CONFIRMED from a real, live capture (a
browser session against the actual site) unless a note says otherwise;
nothing here is a guess at what the site "probably" does.

Scope (deliberately narrow): a single stock's ticker page —
`https://www.tipranks.com/stocks/{ticker}/stock-analysis` — and nothing
else. No screener/stock-list pages, no insider-trading pages, no hedge
fund activity pages; those are real sections of the same site but are out
of scope for this repo.

CONFIRMED, from a real captured session:

- The ticker page is a client-rendered SPA (React + MobX; no
  `__NEXT_DATA__` / `__INITIAL_STATE__` script tag — this is not
  Next.js). It calls a same-origin JSON endpoint on load:
  `https://www.tipranks.com/stocks/{ticker}/stock-analysis/payload.json`.
  That endpoint is what this parser reads; there is no need to scrape
  rendered DOM/CSS for any field this repo cares about.
- The payload's shape: `{"_meta": {...}, "models": {"stocks": [...]}}`.
  `models.stocks` is a LIST that includes the requested ticker's full
  record plus a handful of unrelated "related stocks" sidebar entries
  with only a stripped-down `analystRatings.all.priceTarget` — the
  requested ticker is found by matching `_id` (case-insensitive) against
  the ticker, never by taking the first element.
- A stock record's `analystRatings` has up to five scopes, all
  independently observed: `all` (every analyst), `best` (TipRanks' own
  "Best Performing Analysts" subset), `sevenDaysConsensus`,
  `thirtyDaysConsensus`, and (seen for MSFT but not AAPL — evidently
  present only when there's a rating that recent) `threeDaysConsensus`.
  Each scope that has data carries: `buy`, `hold`, `sell`, `total`, `id`
  (the site's own consensus label, e.g. `"moderateBuy"`, `"neutral"`,
  `"strongBuy"`, `"moderateSell"` — all four confirmed live), `enumId`
  (that label's numeric id — 2/3/4/5 confirmed; 1, presumably
  `"strongSell"`, was never observed in this session's sample of tickers
  and is NOT assumed), `currency`, `priceTarget.{value,upside}`,
  `highPriceTarget`, `lowPriceTarget`.
- A scope's consensus fields (`buy`/`hold`/`sell`/`total`/`id`/`enumId`)
  can be ABSENT even when `priceTarget` is present — confirmed live for
  LUV and T, both of which returned only `{"priceTarget": {...}}` under
  `all` with no buy/hold/sell breakdown at all. This parser treats every
  one of these fields as independently optional; it never assumes that
  seeing `priceTarget` implies seeing a consensus label.
- `smartScore` (`{"update": <date>, "value": <int>}`) is real and
  confirmed (AAPL: 7, MSFT: 9, INTC: 10) but is NOT present on every
  payload — it was absent from the AAPL `stock-forecast/payload.json`
  fetched from the `/forecast` tab; it IS present on the
  `/stock-analysis` tab's payload, which is why that's the tab this
  repo's URLs point at rather than `/forecast`.
- `quotes` carries at least a `pre` (pre-market) and an `open` sub-object
  plus a top-level `tradeTime` naming which one is current, each with its
  own `price`/`change`/`lastTrade`. In this session's captures the quote
  timestamps were noticeably older than the page's own rendered "last
  price" text, which points at the payload being served from a CDN cache
  that lags the live price feed. Treat `current_price` from this parser
  as best-effort / possibly stale, never as a real-time quote.
- Ticker lookup is case-insensitive server-side (`/stocks/MSFT/...` and
  `/stocks/msft/...` both resolve; the canonical rendered URL is
  lowercase). An unknown ticker returns a real HTTP 404. A ticker that
  once existed but no longer resolves (observed for WBA and SAVE, both
  real companies) returns an HTTP 400 — both are "not found" as far as
  this repo is concerned, but are recorded as distinct raw statuses.
- `https://www.tipranks.com/robots.txt` (single `User-agent: *` group,
  captured verbatim below) disallows `/api/*` and a handful of other
  paths, but does NOT disallow anything under `/stocks/*` other than
  `/stocks/*/options-chain/*` (with `/stocks/*/options-chain/unusual-
  options-activity` explicitly re-allowed). Neither
  `/stocks/{ticker}/stock-analysis` nor its `payload.json` matches any
  Disallow rule — `is_allowed()` below evaluates this from the captured
  text itself (longest-match-wins, the documented robots.txt semantics),
  not from a hardcoded conclusion, so a refreshed capture is
  automatically re-checked.
- A same-origin fetch of the payload after a normal page load returned
  clean 200s with real data in every capture this session, with no
  interactive CAPTCHA/challenge page shown and no `cf_clearance` cookie
  present. A Cloudflare bot-management script (`/cdn-cgi/challenge-
  platform/...`) does load and does POST a fingerprint on every page
  view, which means Cloudflare is watching traffic here, even though it
  never blocked a normal browser session in this sample. This is stated
  as "not observed to challenge a normal visit," not "confirmed
  unprotected" — unlike g2.com's DataDome, there is no known solvable
  challenge type to wire up here because none was ever presented.

NOT confirmed / explicitly out of scope:

- `enumId` 1 / a `"strongSell"` id — plausible by symmetry with 2-5, but
  this repo does not hardcode a label table that includes it. The site's
  own `id` string is used verbatim as the label source instead of a
  private enum table, which sidesteps needing to guess the one value
  never observed.
- Non-US-listed tickers, ADRs, or any currency other than USD — every
  ticker sampled this session was USD-denominated.
- Ticker symbols containing `.` or `-` (e.g. share classes) — untested.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

BASE_URL = "https://www.tipranks.com"

# Verbatim capture of https://www.tipranks.com/robots.txt (single
# `User-agent: *` group). Kept as a literal string, not a hand-written
# summary, so `is_allowed()` re-derives the answer from the real rules
# rather than from a conclusion that can go stale silently.
ROBOTS_TXT_CAPTURE = """\
User-agent: *
Sitemap: https://www.tipranks.com/sitemap.xml
Disallow: /bin/
Disallow: /ValueWalk/
Disallow: /downloads/
Disallow: /client/
Disallow: /Views/
Disallow: /thank-you/
Disallow: /news/blurbs/
Disallow: /news/pre-earnings/
Disallow: /news/pre-market-movers/
Disallow: /news/dividends/
Disallow: /news/earnings/
Disallow: /news/market-movers/
Disallow: /class-action/
Disallow: /research-tools/*
Disallow: /smart-investor/home
Disallow: /userdisclaimer
Disallow: /tv/results/*
Disallow: /api/*
Disallow: /challenge/*
Disallow: *?*s=
Disallow: *?*id=
Disallow: /sign-up?redirectTo
Disallow: /sign-in?redirectTo
Allow: /stocks/*/options-chain/unusual-options-activity
Disallow: /stocks/*/options-chain/*
Allow: /insiders
Allow: /insiders/*
Allow: /investors/*
"""


def _robots_rules() -> List[tuple]:
    """Parse ROBOTS_TXT_CAPTURE into (directive, pattern) pairs for the
    (only) `User-agent: *` group. Order preserved; matching is
    longest-pattern-wins per the documented robots.txt convention, not
    first-match or last-match."""
    rules = []
    for line in ROBOTS_TXT_CAPTURE.splitlines():
        line = line.strip()
        if line.lower().startswith("allow:"):
            rules.append(("allow", line.split(":", 1)[1].strip()))
        elif line.lower().startswith("disallow:"):
            value = line.split(":", 1)[1].strip()
            if value:  # a bare "Disallow:" with no value means "allow all"
                rules.append(("disallow", value))
    return rules


_ROBOTS_RULES = _robots_rules()


def _pattern_to_regex(pattern: str) -> re.Pattern:
    # robots.txt "*" matches any sequence; the whole pattern is a prefix
    # match unless it ends in "$" (none of TipRanks' rules do).
    escaped = re.escape(pattern).replace(r"\*", ".*")
    return re.compile("^" + escaped)


def is_allowed(path: str) -> bool:
    """True if `path` (e.g. "/stocks/aapl/stock-analysis") is allowed by
    the captured robots.txt, using longest-matching-rule-wins. No
    matching rule at all means allowed (robots.txt's own default)."""
    best_len = -1
    best_directive = "allow"
    for directive, pattern in _ROBOTS_RULES:
        regex = _pattern_to_regex(pattern)
        if regex.match(path) and len(pattern) > best_len:
            best_len = len(pattern)
            best_directive = directive
    return best_directive == "allow"


def ticker_page_url(ticker: str) -> str:
    return f"{BASE_URL}/stocks/{ticker.lower()}/stock-analysis"


def ticker_payload_url(ticker: str) -> str:
    return f"{BASE_URL}/stocks/{ticker.lower()}/stock-analysis/payload.json"


def humanize_consensus_id(raw_id: Optional[str]) -> Optional[str]:
    """Turn the site's own camelCase consensus id (e.g. "moderateBuy",
    "neutral", "strongBuy", "moderateSell" — all four confirmed live)
    into a display label ("Moderate Buy", "Neutral", ...). Mechanical
    formatting of a real, site-supplied string — never a private
    guess-table for values this repo hasn't seen."""
    if not raw_id:
        return None
    words = re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?=[A-Z]|$)", raw_id)
    return " ".join(w.capitalize() for w in words) if words else raw_id


@dataclass
class TickerRating:
    """One ticker's forecast-page snapshot. Not the family's `Product`
    dataclass (CLAUDE.md §9) — that schema is for marketplace listings
    with a price/brand/sku, none of which exist here. This is a new,
    domain-appropriate schema for analyst-consensus data; the exit-code
    contract in `output_writer.py` is still shared with the rest of the
    2scraper family, since that part is genuinely site-agnostic.

    `consensus_*` / `best_consensus_*` fields mirror the site's own two
    real scopes (`analystRatings.all` and `analystRatings.best`) and are
    never merged into one, the same discipline g2-scraper applies to its
    two distinct rating scales: they measure different analyst
    populations and collapsing them would misrepresent both.
    """

    ticker: str
    source: str
    source_url: str
    scraped_at: str

    company_name: Optional[str] = None
    currency: Optional[str] = None

    smart_score: Optional[int] = None
    smart_score_updated: Optional[str] = None

    # "all analysts" consensus — the headline Buy/Hold/Sell rating.
    consensus_rating_id: Optional[str] = None
    consensus_rating_label: Optional[str] = None
    analyst_count: Optional[int] = None
    buy_count: Optional[int] = None
    hold_count: Optional[int] = None
    sell_count: Optional[int] = None
    price_target_average: Optional[float] = None
    price_target_high: Optional[float] = None
    price_target_low: Optional[float] = None
    price_target_upside: Optional[float] = None  # fraction, e.g. -0.018 = -1.8%

    # "best performing analysts" consensus — TipRanks' own subset.
    best_consensus_rating_id: Optional[str] = None
    best_consensus_rating_label: Optional[str] = None
    best_analyst_count: Optional[int] = None
    best_buy_count: Optional[int] = None
    best_hold_count: Optional[int] = None
    best_sell_count: Optional[int] = None
    best_price_target_average: Optional[float] = None

    # Best-effort; see module docstring's note on stale/cached quotes.
    current_price: Optional[float] = None
    current_price_asof: Optional[str] = None
    current_price_source: Optional[str] = None  # which quotes.<key> was used


def _extract_scope(scope: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    scope = scope or {}
    pt = scope.get("priceTarget") or {}
    return {
        "id": scope.get("id"),
        "total": scope.get("total"),
        "buy": scope.get("buy"),
        "hold": scope.get("hold"),
        "sell": scope.get("sell"),
        "currency": scope.get("currency"),
        "price_target_average": pt.get("value"),
        "price_target_upside": pt.get("upside"),
        "price_target_high": scope.get("highPriceTarget"),
        "price_target_low": scope.get("lowPriceTarget"),
    }


def find_ticker_record(payload: Dict[str, Any], ticker: str) -> Optional[Dict[str, Any]]:
    """Find the requested ticker's own record in a stock-analysis
    payload's `models.stocks` list — never just `stocks[0]`, since that
    list also carries unrelated sidebar entries (confirmed live: AAPL's
    payload included ADTN/AEHR/BELFB/ONTO/SAP alongside AAPL itself)."""
    stocks = ((payload or {}).get("models") or {}).get("stocks") or []
    ticker_upper = ticker.upper()
    for stock in stocks:
        if str(stock.get("_id", "")).upper() == ticker_upper:
            return stock
    return None


def parse_payload(
    payload: Dict[str, Any], ticker: str, *, source_url: str, scraped_at: str
) -> Optional[TickerRating]:
    """Build a TickerRating from an already-fetched, already-JSON-decoded
    stock-analysis payload. Returns None if the ticker's own record isn't
    in the payload at all (the caller should treat that as "not found",
    same as a real 404/400 from the site)."""
    record = find_ticker_record(payload, ticker)
    if record is None:
        return None

    ratings = record.get("analystRatings") or {}
    all_scope = _extract_scope(ratings.get("all"))
    best_scope = _extract_scope(ratings.get("best"))

    smart_score = record.get("smartScore") or {}
    company = record.get("company") or {}

    quotes = record.get("quotes") or {}
    trade_time = quotes.get("tradeTime")
    current_quote = quotes.get(trade_time) if trade_time else None
    if current_quote is None:
        # Fall back to whichever known quote sub-object is present.
        for key in ("open", "pre"):
            if quotes.get(key):
                current_quote = quotes[key]
                trade_time = key
                break

    return TickerRating(
        ticker=ticker.upper(),
        source="tipranks",
        source_url=source_url,
        scraped_at=scraped_at,
        company_name=company.get("name"),
        currency=all_scope["currency"],
        smart_score=smart_score.get("value"),
        smart_score_updated=smart_score.get("update"),
        consensus_rating_id=all_scope["id"],
        consensus_rating_label=humanize_consensus_id(all_scope["id"]),
        analyst_count=all_scope["total"],
        buy_count=all_scope["buy"],
        hold_count=all_scope["hold"],
        sell_count=all_scope["sell"],
        price_target_average=all_scope["price_target_average"],
        price_target_high=all_scope["price_target_high"],
        price_target_low=all_scope["price_target_low"],
        price_target_upside=all_scope["price_target_upside"],
        best_consensus_rating_id=best_scope["id"],
        best_consensus_rating_label=humanize_consensus_id(best_scope["id"]),
        best_analyst_count=best_scope["total"],
        best_buy_count=best_scope["buy"],
        best_hold_count=best_scope["hold"],
        best_sell_count=best_scope["sell"],
        best_price_target_average=best_scope["price_target_average"],
        current_price=(current_quote or {}).get("price"),
        current_price_asof=(current_quote or {}).get("lastTrade"),
        current_price_source=trade_time,
    )
