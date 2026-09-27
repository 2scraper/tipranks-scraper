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
  LUV, T, and KO, all of which returned only `{"priceTarget": {...}}`
  under `all` with no buy/hold/sell breakdown at all. `analystRatings` itself
  (the whole `all`/`best`/*Consensus block) can be missing entirely too —
  confirmed live for GME, whose record had only `smartScore`, nothing
  under `analystRatings` at all. This parser treats every one of these
  fields as independently optional at every level; it never assumes that
  seeing one implies seeing another. LUV's `company.name` is confirmed
  live to be "Southwest Airlines" — an earlier draft of this repo's LUV
  test fixture once carried that same string as a guess from general
  knowledge rather than a real capture, and was caught and stripped
  before it shipped; it's now in the fixture again, this time because a
  live capture actually returned it.
- Ticker symbols containing a dot resolve correctly —
  `/stocks/brk.b/stock-analysis` (Berkshire Hathaway class B) is
  confirmed live, real data, not a 404 — including a live BRK.B capture
  with only 2 covering analysts, a real (if unusual) number for a
  holding company, not a parser artifact. Untested: a `-` in the symbol.
- All of the above, plus CSV output, `--tickers-file`, the zero-result
  exit code with and without `--allow-empty` (writes nothing without the
  flag, an empty array with it — same `EXIT_ZERO_PRODUCTS` either way),
  and `diff_runs.py` against two real runs, were each independently
  confirmed by running the actual shipped `playwright_scraper.py` CLI
  (not a harness calling its internals) from a real machine with real
  internet access, live against tipranks.com. Every one matched its
  documented behavior exactly.
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
- The edge stack is confirmed from real response headers to be TWO
  layers, not one: **Fastly** in front (`via: 1.1 varnish`,
  `x-served-by`, `surrogate-control`/`x-cache` headers — the HTML page is
  cached `max-age=86400`, the `payload.json` endpoint `max-age=300`, both
  with `stale-while-revalidate=86400`), and **Cloudflare** behind/beside
  it (`server: cloudflare`, `cf-ray`, `cf-cache-status`). The Fastly
  cache TTLs are the direct, confirmed explanation for the "quote can lag
  the rendered price" note above — a live quote sitting behind a 5-minute
  (worst case ~24h, on `stale-while-revalidate`) edge cache is expected
  staleness, not a parser bug.
- On Cloudflare's side: a bot-management fingerprinting script
  (`/cdn-cgi/challenge-platform/...`) loads and POSTs a fingerprint on
  every page view, and Google reCAPTCHA's script
  (`recaptcha.net/recaptcha/api.js?render=explicit`) is also loaded on
  the page (present, `render=explicit` — i.e. loaded ready to render on
  demand, not auto-rendered) — so at least two challenge mechanisms are
  present in the page's own JS, even though neither ever actually
  rendered a challenge in this session. No Cloudflare Turnstile script,
  no hCaptcha, and no known DataDome/PerimeterX/Akamai
  Bot-Manager/Imperva/Kasada/Arkose Labs cookie or script was found in
  any capture. Across roughly 20 distinct tickers and 35+ page/payload
  requests this session (spread across the initial research and two
  later follow-up batches — TSLA, GOOGL, BRK.B, GME, CVNA, NVDA, JPM,
  DIS, KO among them), every single one returned a clean 200 with real
  data; none triggered a visible challenge, a CAPTCHA, or a 429. This is
  stated as "not observed to challenge this session's traffic," not
  "confirmed unprotected" — unlike g2.com's DataDome, there is no known
  solvable challenge type to wire up here, because none was ever
  presented to solve.
- A later pre-release audit did observe a real block: an HTTP 403 on the
  page navigation itself, body "Just a moment...", final URL carrying
  Cloudflare's `__cf_chl_rt_tk` challenge-token parameter — a genuine
  Cloudflare JS challenge, not a generic rate-limit 429. Observed in a
  local (non-CI) headless Playwright Chromium run, after an earlier run
  from the same environment had already completed successfully (AAPL, plus
  a confirmed-not-found ticker, both exit 0) — so this reads as a
  behavior/volume-triggered challenge, not a block on the first request.
  `stock-analysis/payload.json` fetched separately still answered 200
  `application/json` in the same session; the challenge was only ever seen
  on the page navigation. Every run after that consistently got `blocked`
  (exit 3), confirming the classification. The engines classify this
  outcome explicitly as `blocked` (exit 3), and the live canary fails
  rather than presenting an availability-green badge with zero data.
- The shipped `parse_payload()` / `output_writer.finish_run()` path
  itself (not just the network fetch) was run end-to-end against five
  freshly captured live payloads (NVDA, TSLA, JPM, DIS, KO): the payload
  bytes came from a real browser session's same-origin `fetch()` against
  `payload.json`, fed straight into this module's `parse_payload()` and
  then `finish_run()`. All five produced `EXIT_OK` with correct field
  extraction, including KO's sparse-consensus case (above) degrading
  exactly as designed. This confirms the parsing/output contract against
  real current data; it does not by itself confirm any of the three
  browser-engine scripts, which still need their own live run with an
  actual installed driver.

NOT confirmed / explicitly out of scope:

- `enumId` 1 / a `"strongSell"` id — plausible by symmetry with 2-5, but
  this repo does not hardcode a label table that includes it. The site's
  own `id` string is used verbatim as the label source instead of a
  private enum table, which sidesteps needing to guess the one value
  never observed.
- Non-US-listed tickers, ADRs, or any currency other than USD — every
  ticker sampled this session was USD-denominated.
- A `-` in a ticker symbol — untested (a `.`, e.g. `BRK.B`, is confirmed
  live above).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

BASE_URL = "https://www.tipranks.com"
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,31}$")

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


def parse_robots_rules(text: str) -> List[tuple]:
    """Parse robots.txt text into (directive, pattern) pairs for the
    (only) `User-agent: *` group. Order preserved; matching is
    longest-pattern-wins per the documented robots.txt convention, not
    first-match or last-match."""
    rules = []
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("allow:"):
            rules.append(("allow", line.split(":", 1)[1].strip()))
        elif line.lower().startswith("disallow:"):
            value = line.split(":", 1)[1].strip()
            if value:  # a bare "Disallow:" with no value means "allow all"
                rules.append(("disallow", value))
    return rules


_ROBOTS_RULES = parse_robots_rules(ROBOTS_TXT_CAPTURE)


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


def normalize_ticker(raw: str) -> str:
    """Return a canonical ticker safe to interpolate into a URL path.

    TipRanks is case-insensitive and a dot is confirmed live (BRK.B). A
    hyphen is accepted as a conventional ticker separator, while slashes,
    query delimiters, whitespace inside the symbol, and dot-segments are
    rejected so the URL checked against robots.txt is the URL the browser
    actually requests.
    """
    ticker = raw.strip().upper()
    if not TICKER_RE.fullmatch(ticker) or ".." in ticker:
        raise ValueError(
            f"invalid ticker {raw!r}: expected 1-32 letters, digits, dots or hyphens"
        )
    return ticker


def ticker_page_url(ticker: str) -> str:
    return f"{BASE_URL}/stocks/{normalize_ticker(ticker).lower()}/stock-analysis"


def ticker_payload_url(ticker: str) -> str:
    return f"{BASE_URL}/stocks/{normalize_ticker(ticker).lower()}/stock-analysis/payload.json"


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
    scope = scope if isinstance(scope, dict) else {}
    pt = scope.get("priceTarget")
    pt = pt if isinstance(pt, dict) else {}
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
    if not isinstance(payload, dict):
        return None
    models = payload.get("models")
    if not isinstance(models, dict):
        return None
    stocks = models.get("stocks")
    if not isinstance(stocks, list):
        return None
    ticker_upper = normalize_ticker(ticker)
    for stock in stocks:
        if not isinstance(stock, dict):
            continue
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

    ratings = record.get("analystRatings")
    ratings = ratings if isinstance(ratings, dict) else {}
    all_scope = _extract_scope(ratings.get("all"))
    best_scope = _extract_scope(ratings.get("best"))

    smart_score = record.get("smartScore")
    smart_score = smart_score if isinstance(smart_score, dict) else {}
    company = record.get("company")
    company = company if isinstance(company, dict) else {}

    quotes = record.get("quotes")
    quotes = quotes if isinstance(quotes, dict) else {}
    trade_time = quotes.get("tradeTime")
    current_quote = quotes.get(trade_time) if isinstance(trade_time, str) else None
    current_quote = current_quote if isinstance(current_quote, dict) else None
    if current_quote is None:
        # Fall back to whichever known quote sub-object is present.
        for key in ("open", "pre"):
            if isinstance(quotes.get(key), dict):
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
