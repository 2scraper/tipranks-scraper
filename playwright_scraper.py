#!/usr/bin/env python3
"""playwright_scraper.py — primary (and, for now, only) engine for
tipranks-scraper. Scrapes one thing: a stock ticker's forecast-page
snapshot (Smart Score, analyst consensus, price target, Buy/Hold/Sell
rating) from tipranks.com's free, unauthenticated data. No account, no
proxy, no captcha solving — none of that is needed for this page, per
`tipranks_parser.py`'s module docstring.

Flow per ticker, confirmed live during research for this repo: navigate
to `https://www.tipranks.com/stocks/{ticker}/stock-analysis`, then fetch
the same-origin `.../stock-analysis/payload.json` from inside the page
(a same-origin `fetch()`, not a separate cookie-less HTTP client) and
parse the JSON. No DOM/CSS scraping is used for any field this repo
cares about.

The handful of engine constants allowed to vary per site (CLAUDE.md §5)
are directly below; nothing else here should need site-specific tuning.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - exercised by smoke_test.py with no engine installed
    sync_playwright = None

from output_writer import EXIT_BAD_USAGE, EXIT_CRASH, finish_run
from tipranks_parser import (
    is_allowed,
    normalize_ticker,
    parse_payload,
    ticker_page_url,
    ticker_payload_url,
    TickerRating,
)

# --- site-specific engine constants (CLAUDE.md §5) ---
NAV_TIMEOUT_MS = 30_000
READINESS_WAIT_MS = 2_000  # the SPA's own payload fetch happens right after load


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_BLOCKED_STATUSES = (403, 429)  # standard "you are blocked/rate-limited" statuses —
# never observed live against this site (see tipranks_parser.py's module
# docstring), but a real, well-known signal worth distinguishing from a
# generic remote_api_error the moment it does show up, rather than
# inventing a CAPTCHA/challenge-HTML heuristic this project has never
# actually seen fire.
_NOT_FOUND_STATUSES = (404, 400)


def fetch_one(page, ticker: str) -> Tuple[Optional[TickerRating], Optional[str]]:
    """Returns (rating_or_None, error_kind). error_kind is None on
    success, "not_found" for a real 404/400, "blocked" for a real 403/429,
    or "remote_api_error" for anything else (nav timeout, non-JSON
    response, etc.) — never raises, so one bad ticker can't take a
    multi-ticker run down with it (CLAUDE.md §6: a failed unit of work
    degrades, it doesn't crash the run)."""
    page_url = ticker_page_url(ticker)
    payload_url = ticker_payload_url(ticker)

    try:
        response = page.goto(page_url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
    except Exception:
        return None, "remote_api_error"

    if response is not None and response.status in _BLOCKED_STATUSES:
        return None, "blocked"
    if response is not None and response.status in _NOT_FOUND_STATUSES:
        return None, "not_found"

    try:
        page.wait_for_timeout(READINESS_WAIT_MS)
        payload = page.evaluate(
            """async (url) => {
                const res = await fetch(url, {credentials: 'include'});
                if (!res.ok) { return {__status: res.status}; }
                return await res.json();
            }""",
            payload_url,
        )
    except Exception:
        return None, "remote_api_error"

    if isinstance(payload, dict) and payload.get("__status") in _BLOCKED_STATUSES:
        return None, "blocked"
    if isinstance(payload, dict) and payload.get("__status") in _NOT_FOUND_STATUSES:
        return None, "not_found"
    if not isinstance(payload, dict) or "models" not in payload:
        return None, "remote_api_error"

    try:
        rating = parse_payload(
            payload, ticker, source_url=page_url, scraped_at=_now_iso()
        )
    except (TypeError, ValueError, AttributeError):
        return None, "remote_api_error"
    if rating is None:
        return None, "not_found"
    return rating, None


def run(
    tickers: List[str], *, headless: bool = True, executable_path: Optional[str] = None
) -> Tuple[List[TickerRating], List[str], List[str], List[str]]:
    """Returns (ratings, tickers_completed, failed_tickers,
    blocked_tickers). A ticker is "completed" whether it resolved to a
    rating or a confirmed not-found; a "blocked" (403/429) or generic
    remote/engine error both count as a failure that could make the run
    `partial` — `blocked_tickers` is the subset of `failed_tickers` that
    were specifically a block, so `main()` can set `finish_run`'s
    `blocked` flag (previously always hardcoded False — see
    `output_writer.finish_run`'s docstring for the bug this fixes).
    `executable_path`, if given, is passed straight to
    `chromium.launch()` — normally unnecessary (Playwright's own bundled
    Chromium download has been reliable in this project's testing), but
    kept for parity with `puppeteer_scraper.py`, where the equivalent
    flag is a real, documented fix for a confirmed-live engine bug (see
    that file's module docstring)."""
    if sync_playwright is None:
        raise RuntimeError("playwright is not installed")

    ratings: List[TickerRating] = []
    completed: List[str] = []
    failed: List[str] = []
    blocked: List[str] = []

    with sync_playwright() as pw:
        launch_kwargs = {"headless": headless}
        if executable_path:
            launch_kwargs["executable_path"] = executable_path
        browser = pw.chromium.launch(**launch_kwargs)
        try:
            page = browser.new_page()
            for ticker in tickers:
                rating, error_kind = fetch_one(page, ticker)
                if error_kind == "blocked":
                    failed.append(ticker)
                    blocked.append(ticker)
                    continue
                if error_kind == "remote_api_error":
                    failed.append(ticker)
                    continue
                completed.append(ticker)
                if rating is not None:
                    ratings.append(rating)
                # error_kind == "not_found" is a completed (not failed)
                # ticker with zero rows contributed — same as g2-scraper
                # treating a confirmed empty category as complete, not
                # partial.
        finally:
            browser.close()

    return ratings, completed, failed, blocked


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--ticker", action="append", dest="tickers", default=[],
        help="A ticker symbol, e.g. AAPL. Repeatable.",
    )
    parser.add_argument(
        "--tickers-file", default=None,
        help="Path to a text file with one ticker per line.",
    )
    parser.add_argument("--format", choices=["json", "csv"], default="json")
    parser.add_argument("--out", default="tipranks_results.json")
    parser.add_argument(
        "--allow-empty", action="store_true",
        help="Write output even if zero ratings were collected.",
    )
    parser.add_argument(
        "--headed", action="store_true",
        help="Launch a visible browser instead of headless (debugging).",
    )
    parser.add_argument(
        "--executable-path", default=None,
        help="Use an already-installed Chrome/Chromium instead of this engine's "
        "own bundled-browser download.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    tickers = list(args.tickers)
    if args.tickers_file:
        try:
            with open(args.tickers_file, "r", encoding="utf-8") as f:
                tickers.extend(line.strip() for line in f if line.strip())
        except OSError as exc:
            print(f"error: could not read --tickers-file: {exc}", file=sys.stderr)
            return EXIT_BAD_USAGE

    try:
        tickers = list(dict.fromkeys(normalize_ticker(t) for t in tickers if t.strip()))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_BAD_USAGE
    if not tickers:
        print("error: no tickers given (use --ticker or --tickers-file)", file=sys.stderr)
        return EXIT_BAD_USAGE

    for ticker in tickers:
        path = f"/stocks/{ticker.lower()}/stock-analysis"
        if not is_allowed(path):
            print(f"error: robots.txt disallows {path}", file=sys.stderr)
            return EXIT_BAD_USAGE

    started_at = time.time()
    try:
        ratings, completed, failed, blocked_tickers = run(
            tickers, headless=not args.headed, executable_path=args.executable_path
        )
    except Exception as exc:  # a real crash, not a per-ticker failure
        print(f"crash: {exc}", file=sys.stderr)
        return EXIT_CRASH

    return finish_run(
        ratings=ratings,
        out_path=args.out,
        fmt=args.format,
        engine="playwright",
        tickers_requested=tickers,
        tickers_completed=completed,
        failed_tickers=failed,
        blocked=bool(blocked_tickers),
        # Preserve remote > blocked precedence if different tickers in a
        # total failure hit different failure kinds. finish_run also derives
        # the plain all-remote case as a defensive backstop.
        remote_api_error=(
            bool(set(failed) - set(blocked_tickers)) and not completed
        ),
        allow_empty=args.allow_empty,
        started_at=started_at,
    )


if __name__ == "__main__":
    sys.exit(main())
