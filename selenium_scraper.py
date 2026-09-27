#!/usr/bin/env python3
"""selenium_scraper.py — Selenium engine for tipranks-scraper. Same CLI
flags, exit codes, and `TickerRating` schema as `playwright_scraper.py`
(CLAUDE.md §4); see that file's module docstring for the shared flow.

**Named per-engine limitation**: chromedriver's `driver.get()` does not
expose the navigated response's HTTP status code the way Playwright's
`page.goto()` / pyppeteer's `page.goto()` do. Rather than reach for
`execute_cdp_cmd`'s lower-level Network domain (real complexity for a
single status code), this engine detects a not-found page the same way a
human would: the confirmed real page title for an unknown ticker is
literally `"Error 404: Page Not Found - TipRanks.com"` (see
`tipranks_parser.py`'s module docstring), so `fetch_one()` below checks
`driver.title` for that string. This is a real, confirmed string, not a
guess — but it is a different detection mechanism from the other two
engines, and is called out here rather than left for a user to discover.

Selenium Manager's own anonymous usage-stat phone-home
(`SE_AVOID_STATS`) is disabled by default below — see SECURITY.md.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

os.environ.setdefault("SE_AVOID_STATS", "true")

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
except ImportError:  # pragma: no cover - exercised by smoke_test.py with no engine installed
    webdriver = None
    Options = None

import output_writer
from output_writer import EXIT_BAD_USAGE, EXIT_CRASH, finish_run
from tipranks_parser import (
    is_allowed,
    parse_payload,
    ticker_page_url,
    ticker_payload_url,
    TickerRating,
)

ENGINE_NAME = "selenium"

# --- site-specific engine constants (CLAUDE.md §5) ---
NAV_TIMEOUT_S = 30
READINESS_WAIT_S = 2  # the SPA's own payload fetch happens right after load

NOT_FOUND_TITLE_MARKER = "Error 404: Page Not Found"

_FETCH_ASYNC_SCRIPT = """
var url = arguments[0];
var callback = arguments[arguments.length - 1];
fetch(url, {credentials: 'include'})
    .then(function (res) {
        if (!res.ok) { callback({__status: res.status}); return; }
        return res.json().then(callback);
    })
    .catch(function (e) { callback({__error: String(e)}); });
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_one(driver, ticker: str) -> Tuple[Optional[TickerRating], Optional[str]]:
    """Same contract as playwright_scraper.fetch_one: (rating_or_None,
    error_kind), never raises."""
    page_url = ticker_page_url(ticker)
    payload_url = ticker_payload_url(ticker)

    try:
        driver.set_page_load_timeout(NAV_TIMEOUT_S)
        driver.get(page_url)
    except Exception:
        return None, "remote_api_error"

    if NOT_FOUND_TITLE_MARKER in (driver.title or ""):
        return None, "not_found"

    time.sleep(READINESS_WAIT_S)

    try:
        driver.set_script_timeout(NAV_TIMEOUT_S)
        payload = driver.execute_async_script(_FETCH_ASYNC_SCRIPT, payload_url)
    except Exception:
        return None, "remote_api_error"

    if isinstance(payload, dict) and payload.get("__status") in (404, 400):
        return None, "not_found"
    if not isinstance(payload, dict) or "models" not in payload:
        return None, "remote_api_error"

    rating = parse_payload(payload, ticker, source_url=page_url, scraped_at=_now_iso())
    if rating is None:
        return None, "not_found"
    return rating, None


def _make_driver(headless: bool, executable_path: Optional[str] = None):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    if executable_path:
        # This is the browser binary (Chrome/Chromium itself), not
        # chromedriver — Selenium calls that `binary_location`, unlike
        # Playwright's/pyppeteer's `executable_path`/`executablePath` for
        # the same concept. Kept for CLI parity with the other two
        # engines (CLAUDE.md §4); this engine hasn't needed it in this
        # project's own testing (see puppeteer_scraper.py's module
        # docstring for the engine that did).
        options.binary_location = executable_path
    return webdriver.Chrome(options=options)


def run(
    tickers: List[str], *, headless: bool = True, executable_path: Optional[str] = None
) -> Tuple[List[TickerRating], List[str], List[str]]:
    if webdriver is None:
        raise RuntimeError("selenium is not installed")

    ratings: List[TickerRating] = []
    completed: List[str] = []
    failed: List[str] = []

    driver = _make_driver(headless, executable_path)
    try:
        for ticker in tickers:
            rating, error_kind = fetch_one(driver, ticker)
            if error_kind == "remote_api_error":
                failed.append(ticker)
                continue
            completed.append(ticker)
            if rating is not None:
                ratings.append(rating)
    finally:
        driver.quit()

    return ratings, completed, failed


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ticker", action="append", dest="tickers", default=[])
    parser.add_argument("--tickers-file", default=None)
    parser.add_argument("--format", choices=["json", "csv"], default="json")
    parser.add_argument("--out", default="tipranks_results.json")
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument(
        "--executable-path",
        default=None,
        help="Use an already-installed Chrome/Chromium instead of this engine's "
        "own bundled-browser download (maps to Selenium's binary_location).",
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

    tickers = list(dict.fromkeys(t.upper() for t in tickers if t.strip()))
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
        ratings, completed, failed = run(
            tickers, headless=not args.headed, executable_path=args.executable_path
        )
    except Exception as exc:
        print(f"crash: {exc}", file=sys.stderr)
        return EXIT_CRASH

    return finish_run(
        ratings=ratings,
        out_path=args.out,
        fmt=args.format,
        engine=ENGINE_NAME,
        tickers_requested=tickers,
        tickers_completed=completed,
        failed_tickers=failed,
        blocked=False,
        remote_api_error=False,
        allow_empty=args.allow_empty,
        started_at=started_at,
    )


if __name__ == "__main__":
    sys.exit(main())
