#!/usr/bin/env python3
"""tipranks_parser.py — the only file in this repo carrying tipranks.com
knowledge. Every fact below is CONFIRMED from a real, live capture (a
browser session against the actual site) unless a note says otherwise;
nothing here is a guess at what the site "probably" does.

Scope (deliberately narrow): a single stock's ticker page —
`https://www.tipranks.com/stocks/{ticker}/stock-analysis` — and nothing
else. No screener/stock-list pages, no insider-trading pages, no hedge
fund activity pages; those are real sections of the same site but are out
of scope for this repo. The ticker's own payload does carry summaries of
dividends, earnings, insider / hedge fund / retail-investor activity and
news / blogger sentiment, and those are read (see "Beyond the analyst
consensus" below); the per-transaction lists behind them are not.

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
  no other captcha widget script, and no known DataDome/PerimeterX/Akamai
  Bot-Manager/Imperva/Kasada/Arkose Labs cookie or script was found in
  any capture. Across roughly 20 distinct tickers and 35+ page/payload
  requests this session (spread across the initial research and two
  later follow-up batches — TSLA, GOOGL, BRK.B, GME, CVNA, NVDA, JPM,
  DIS, KO among them), every single one returned a clean 200 with real
  data; none triggered a visible challenge, a CAPTCHA, or a 429. This is
  stated as "not observed to challenge this session's traffic," not
  "confirmed unprotected" — there is no known solvable challenge type to wire up here, because none was ever
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

Beyond the analyst consensus, CONFIRMED live on 2026-10-20 (AAPL, TSLA,
NVDA, KO, GME, BRK.B, LUV, AMZN), all on the ticker's own record:

- `lastDividend` / `nextDividend`: `amount`, `executionDate` (ex-date),
  `payDate`, `payoutInterval`, `annualizedPayout`; `dividend.yield` and
  `dividend.payoutRatio`. Absent (null) for non-payers (TSLA, AMZN,
  BRK.B); `nextDividend` is usually null, set once declared (KO). A
  suspended payer keeps its last dividend (GME: 2019) with a null yield,
  so `dividend_ex_date` is what tells a current payer apart.
- `lastEarning` / `nextEarning`: `reported` (the report date),
  `reportedEPS` (null until reported), `value` (consensus EPS estimate),
  `reportOnTimeOfDay` ("PreMarket", "AfterHours" or "Unknown"),
  `salesEstimate`.
- `insiderActivity`: `sentiment`, `signal` (0-1) and `shareTrend`, a
  signed USD value of insider buying (+) or selling (-). All null, with
  `shareTrend` 0, when there is nothing to report (AAPL, TSLA).
- `hedgeFundActivity`: `sentiment`, `signal` (0-1) and `trend`, the net
  change in shares held (signed).
- `investorActivity.all`: TipRanks' retail-portfolio `sentiment`,
  `score` (0-1) and `change.days7` / `change.days30` (change in the share
  of portfolios holding it, a fraction).
- `newsSentiment`: `sentiment`, `score` (0-1), `positive` (bullish share
  of articles), `buzz.lastWeek` (articles last week), `buzz.weeklyAvg`.
- `bloggerOpinions.consensus`: `signal`, `ratioPositive`, `total`.
- Every `sentiment` / `signal` label seen: "veryNegative", "negative",
  "neutral", "positive", "veryPositive". Passed through verbatim.
- The periods behind the insider and hedge fund figures are not stated
  in the payload; they are the numbers the site's own cards show.

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
from typing import Any, Dict, List, Optional, Tuple

BASE_URL = "https://www.tipranks.com"
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,31}$")

# The ONLY real block this repo has ever observed (see the module
# docstring's "A later pre-release audit" note above, and CHANGELOG.md
# 0.2.0): an HTTP 403 with body text "Just a moment..." AND a final,
# post-redirect URL carrying Cloudflare's `__cf_chl_rt_tk` query
# parameter — never a captcha widget (Turnstile, reCAPTCHA v2 or other),
# none of which has ever been seen here. Kept as two SEPARATE constants, not one combined list,
# because they were confirmed in two different places, not one: the
# marker text is in the response BODY, `__cf_chl_rt_tk` is in the final
# URL after Cloudflare's own redirect, not confirmed to also appear
# anywhere in that response's HTML — folding both into one `extra_
# markers` list handed to `captcha_solver.detect_from_html(body, ...)`
# would silently just never match on the URL half. Added 2026-09-28.
# `_cf_chl_opt` is the challenge page's own inline config, present whatever
# language the page is shown in: a Chrome with a Russian locale gets the
# title "Один момент…", which "just a moment" never matched (seen live
# 2026-10-19). `cdn-cgi/challenge-platform` is NOT usable here: Cloudflare
# also loads it on normal, unblocked pages.
BLOCK_BODY_MARKERS = ("just a moment", "_cf_chl_opt")
BLOCK_URL_MARKERS = ("__cf_chl_rt_tk",)

# Verbatim capture of https://www.tipranks.com/robots.txt (single
# `User-agent: *` group), re-captured 2026-10-20. Kept as a literal string, not a hand-written
# summary, so `is_allowed()` re-derives the answer from the real rules
# rather than from a conclusion that can go stale silently.
ROBOTS_TXT_CAPTURE = """\
# robotstxt.org

User-agent: *
Sitemap: https://www.tipranks.com/sitemap.xml
Disallow: /bin/
Disallow: /ValueWalk/
Disallow: /downloads/
Disallow: /client/
Disallow: /Views/
Disallow: /thank-you/
Disallow: /news/pre-market-movers/
Disallow: /news/market-movers/
Disallow: /class-action/
Disallow: /research-tools/*
Disallow: /smart-investor/home
Disallow: /userdisclaimer
Disallow: /tv/results/*
Disallow: /api/*
Disallow: /challenge/*
Disallow: /*?*s=
Disallow: /*?*id=
Disallow: /*?hash=
Disallow: /*?llf=
Disallow: /sign-up?redirectTo
Disallow: /sign-in?redirectTo
Allow: /stocks/*/options-chain/unusual-options-activity
Disallow: /stocks/*/options-chain/*
Allow: /insiders
Allow: /insiders/*
Allow: /investors/*
Sitemap:https://www.tipranks.com/sitemap-articles-news/index.xml
Sitemap:https://www.tipranks.com/sitemap-articles-news/recent.xml
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


def read_tickers_file(path: str) -> List[str]:
    """Read a --tickers-file: one ticker per line.

    Blank lines and ``#`` comments are skipped, whole-line ones like
    --proxy-file's and trailing ones (``AAPL  # Apple``) too; ``#`` can
    never be part of a ticker (see normalize_ticker). Raises OSError when
    the file cannot be read.
    """
    with open(path, "r", encoding="utf-8") as f:
        return [t for t in (line.split("#", 1)[0].strip() for line in f) if t]


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
    dataclass — that schema is for marketplace listings
    with a price/brand/sku, none of which exist here. This is a new,
    domain-appropriate schema for analyst-consensus data; the exit-code
    contract in `output_writer.py` is still shared with the rest of the
    2scraper family, since that part is genuinely site-agnostic.

    `consensus_*` / `best_consensus_*` fields mirror the site's own two
    real scopes (`analystRatings.all` and `analystRatings.best`) and are
    never merged into one: they measure different analyst
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
    current_price_source: Optional[str] = None  # "open"/"pre" (a quotes.<key>), "daily" or "daily_history"

    # Everything below is appended after the original columns so an
    # existing CSV consumer's column positions stay put. Dates are the
    # site's own ISO strings; sentiment labels its own ids (module
    # docstring, "Beyond the analyst consensus").
    dividend_amount: Optional[float] = None  # the latest declared dividend, per payment
    dividend_ex_date: Optional[str] = None
    dividend_pay_date: Optional[str] = None
    dividend_frequency: Optional[str] = None  # e.g. "quarterly"
    dividend_annualized_payout: Optional[float] = None
    dividend_yield: Optional[float] = None  # fraction
    dividend_payout_ratio: Optional[float] = None  # fraction
    next_dividend_amount: Optional[float] = None  # only once declared
    next_dividend_ex_date: Optional[str] = None
    next_dividend_pay_date: Optional[str] = None

    last_earnings_date: Optional[str] = None
    last_earnings_eps: Optional[float] = None
    last_earnings_eps_estimate: Optional[float] = None
    next_earnings_date: Optional[str] = None
    next_earnings_time: Optional[str] = None  # "PreMarket", "AfterHours" or "Unknown"
    next_earnings_eps_estimate: Optional[float] = None
    next_earnings_revenue_estimate: Optional[float] = None

    insider_sentiment: Optional[str] = None
    insider_signal: Optional[float] = None  # 0-1
    insider_net_usd: Optional[float] = None  # + buying, - selling
    hedge_fund_sentiment: Optional[str] = None
    hedge_fund_signal: Optional[float] = None  # 0-1
    hedge_fund_net_shares: Optional[float] = None  # net change in shares held
    investor_sentiment: Optional[str] = None  # TipRanks retail portfolios
    investor_signal: Optional[float] = None  # 0-1
    investor_change_7d: Optional[float] = None  # change in share of portfolios holding it
    investor_change_30d: Optional[float] = None
    news_sentiment: Optional[str] = None
    news_sentiment_score: Optional[float] = None  # 0-1
    news_bullish_ratio: Optional[float] = None  # fraction of articles
    news_articles_last_week: Optional[int] = None
    news_articles_weekly_average: Optional[float] = None
    blogger_sentiment: Optional[str] = None
    blogger_bullish_ratio: Optional[float] = None
    blogger_opinion_count: Optional[int] = None


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


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _extract_extras(record: Dict[str, Any]) -> Dict[str, Any]:
    """The dividend / earnings / activity / sentiment columns of
    TickerRating, from the ticker's own record. A missing or null block
    leaves its columns None."""
    dividend = _dict(record.get("dividend"))
    last_div = _dict(record.get("lastDividend")) or dividend
    next_div = _dict(record.get("nextDividend"))
    last_earn = _dict(record.get("lastEarning"))
    next_earn = _dict(record.get("nextEarning"))
    insider = _dict(record.get("insiderActivity"))
    hedge = _dict(record.get("hedgeFundActivity"))
    investor = _dict(_dict(record.get("investorActivity")).get("all"))
    investor_change = _dict(investor.get("change"))
    news = _dict(record.get("newsSentiment"))
    buzz = _dict(news.get("buzz"))
    blogger = _dict(_dict(record.get("bloggerOpinions")).get("consensus"))
    return {
        "dividend_amount": last_div.get("amount"),
        "dividend_ex_date": last_div.get("executionDate"),
        "dividend_pay_date": last_div.get("payDate"),
        "dividend_frequency": last_div.get("payoutInterval"),
        "dividend_annualized_payout": last_div.get("annualizedPayout"),
        "dividend_yield": dividend.get("yield"),
        "dividend_payout_ratio": dividend.get("payoutRatio"),
        "next_dividend_amount": next_div.get("amount"),
        "next_dividend_ex_date": next_div.get("executionDate"),
        "next_dividend_pay_date": next_div.get("payDate"),
        "last_earnings_date": last_earn.get("reported"),
        "last_earnings_eps": last_earn.get("reportedEPS"),
        "last_earnings_eps_estimate": last_earn.get("value"),
        "next_earnings_date": next_earn.get("reported"),
        "next_earnings_time": next_earn.get("reportOnTimeOfDay"),
        "next_earnings_eps_estimate": next_earn.get("value"),
        "next_earnings_revenue_estimate": next_earn.get("salesEstimate"),
        "insider_sentiment": insider.get("sentiment"),
        "insider_signal": insider.get("signal"),
        "insider_net_usd": insider.get("shareTrend"),
        "hedge_fund_sentiment": hedge.get("sentiment"),
        "hedge_fund_signal": hedge.get("signal"),
        "hedge_fund_net_shares": hedge.get("trend"),
        "investor_sentiment": investor.get("sentiment"),
        "investor_signal": investor.get("score"),
        "investor_change_7d": investor_change.get("days7"),
        "investor_change_30d": investor_change.get("days30"),
        "news_sentiment": news.get("sentiment"),
        "news_sentiment_score": news.get("score"),
        "news_bullish_ratio": news.get("positive"),
        "news_articles_last_week": buzz.get("lastWeek"),
        "news_articles_weekly_average": buzz.get("weeklyAvg"),
        "blogger_sentiment": blogger.get("signal"),
        "blogger_bullish_ratio": blogger.get("ratioPositive"),
        "blogger_opinion_count": blogger.get("total"),
    }


def find_ticker_record(payload: Dict[str, Any], ticker: str) -> Optional[Dict[str, Any]]:
    """Find the requested ticker's own record in a stock-analysis
    payload's `models.stocks` list — never just `stocks[0]`, since that
    list also carries unrelated sidebar entries (confirmed live: AAPL's
    payload included ADTN/AEHR/BELFB/ONTO/SAP alongside AAPL itself).

    The ticker can also appear in its own sidebar, as a stub (name and
    price target only) listed BEFORE its full record: live on 2026-10-20
    that was 55 of the S&P 500 (ABNB, AXP, BKNG, ...), and taking the
    first match lost their Smart Score and consensus. When several
    records match, the one with the most fields wins."""
    if not isinstance(payload, dict):
        return None
    models = payload.get("models")
    if not isinstance(models, dict):
        return None
    stocks = models.get("stocks")
    if not isinstance(stocks, list):
        return None
    ticker_upper = normalize_ticker(ticker)
    matches = [
        stock for stock in stocks
        if isinstance(stock, dict) and str(stock.get("_id", "")).upper() == ticker_upper
    ]
    return max(matches, key=len, default=None)


def missing_record_kind(payload: Any) -> str:
    """Why `parse_payload` found no record for a ticker, for a payload the
    site served with a 200. An unknown ticker is answered by the site with
    a 404/400 before any payload is read, so a served payload without the
    ticker's record only means "not found" when its stock list is present
    and empty. A stock list carrying other entries but not this ticker, or
    no stock list at all, means the payload's shape no longer matches this
    parser: that is a parser failure, never an empty result."""
    models = payload.get("models") if isinstance(payload, dict) else None
    stocks = models.get("stocks") if isinstance(models, dict) else None
    if isinstance(stocks, list) and not stocks:
        return "not_found"
    return "parser_failed"


def _freshest_price(
    payload: Dict[str, Any], record: Dict[str, Any], ticker: str
) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    """(price, as-of, source) from the most recent of the payload's price
    sources. `quotes.<tradeTime>` alone is not enough: live on 2026-10-19
    it was weeks or months old where present (AAPL 2026-08-31, NVDA
    2026-07-27) and absent for MSFT, GME, LUV and BRK.B, while the site's
    own `priceTarget.upside` was computed from the last trading day's
    close, carried in `prices.daily` and as the last entry of
    `stocks_extra[].prices.daily`. Source is the quote key ("open"/"pre"),
    "daily", or "daily_history"."""
    candidates = []

    quotes = record.get("quotes")
    quotes = quotes if isinstance(quotes, dict) else {}
    trade_time = quotes.get("tradeTime")
    keys = [trade_time] if isinstance(trade_time, str) else []
    for key in keys + ["open", "pre"]:
        quote = quotes.get(key)
        if isinstance(quote, dict) and isinstance(quote.get("price"), (int, float)):
            candidates.append((quote.get("lastTrade") or "", quote["price"], key))
            break

    prices = record.get("prices")
    daily = prices.get("daily") if isinstance(prices, dict) else None
    if isinstance(daily, dict) and isinstance(daily.get("price"), (int, float)):
        candidates.append((daily.get("date") or "", daily["price"], "daily"))

    models = payload.get("models") if isinstance(payload, dict) else None
    extras = models.get("stocks_extra") if isinstance(models, dict) else None
    for extra in extras if isinstance(extras, list) else []:
        if not isinstance(extra, dict) or str(extra.get("_id", "")).upper() != normalize_ticker(ticker):
            continue
        history = extra.get("prices", {}).get("daily") if isinstance(extra.get("prices"), dict) else None
        rows = [r for r in history if isinstance(r, dict) and r.get("date")] if isinstance(history, list) else []
        if rows:
            last = max(rows, key=lambda r: r["date"])
            price = last.get("close") if isinstance(last.get("close"), (int, float)) else last.get("price")
            if isinstance(price, (int, float)):
                candidates.append((last["date"], price, "daily_history"))
        break

    if not candidates:
        return None, None, None
    asof, price, source = max(candidates, key=lambda c: c[0])
    return price, asof or None, source


def parse_payload(
    payload: Dict[str, Any], ticker: str, *, source_url: str, scraped_at: str
) -> Optional[TickerRating]:
    """Build a TickerRating from an already-fetched, already-JSON-decoded
    stock-analysis payload. Returns None if the ticker's own record isn't
    in the payload at all; `missing_record_kind()` then says whether that
    is a genuine "not found" or a parser failure."""
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

    current_price, current_price_asof, current_price_source = _freshest_price(payload, record, ticker)

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
        current_price=current_price,
        current_price_asof=current_price_asof,
        current_price_source=current_price_source,
        **_extract_extras(record),
    )
