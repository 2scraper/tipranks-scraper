#!/usr/bin/env python3
"""puppeteer_scraper.py — Puppeteer (via pyppeteer) engine for
tipranks-scraper. Same CLI flags, exit codes, and `TickerRating` schema
as `playwright_scraper.py` (CLAUDE.md §4); see that file's module
docstring for the shared flow. Unlike Selenium, pyppeteer's `page.goto()`
returns a real response object with `.status`, so not-found detection
here matches Playwright's (a real HTTP 404/400), not the title-string
fallback `selenium_scraper.py` needs.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

try:
    from pyppeteer import launch as pyppeteer_launch
except ImportError:  # pragma: no cover - exercised by smoke_test.py with no engine installed
    pyppeteer_launch = None

import output_writer
from output_writer import EXIT_BAD_USAGE, EXIT_CRASH, finish_run
from tipranks_parser import (
    is_allowed,
    parse_payload,
    ticker_page_url,
    ticker_payload_url,
    TickerRating,
)

ENGINE_NAME = "puppeteer"

# --- site-specific engine constants (CLAUDE.md §5) ---
NAV_TIMEOUT_MS = 30_000
READINESS_WAIT_MS = 2_000  # the SPA's own payload fetch happens right after load

_FETCH_SCRIPT = """async (url) => {
    try {
        const res = await fetch(url, {credentials: 'include'});
        if (!res.ok) { return {__status: res.status}; }
        return await res.json();
    } catch (e) {
        return {__error: String(e)};
    }
}"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def fetch_one_async(page, ticker: str) -> Tuple[Optional[TickerRating], Optional[str]]:
    """Same contract as playwright_scraper.fetch_one: (rating_or_None,
    error_kind), never raises."""
    page_url = ticker_page_url(ticker)
    payload_url = ticker_payload_url(ticker)

    try:
        response = await page.goto(page_url, timeout=NAV_TIMEOUT_MS, waitUntil="domcontentloaded")
    except Exception:
        return None, "remote_api_error"

    if response is not None and response.status in (404, 400):
        return None, "not_found"

    await asyncio.sleep(READINESS_WAIT_MS / 1000)

    try:
        payload = await page.evaluate(_FETCH_SCRIPT, payload_url)
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


async def run_async(
    tickers: List[str], *, headless: bool = True
) -> Tuple[List[TickerRating], List[str], List[str]]:
    if pyppeteer_launch is None:
        raise RuntimeError("pyppeteer is not installed")

    ratings: List[TickerRating] = []
    completed: List[str] = []
    failed: List[str] = []

    browser = await pyppeteer_launch(headless=headless, args=["--no-sandbox"])
    try:
        page = await browser.newPage()
        for ticker in tickers:
            rating, error_kind = await fetch_one_async(page, ticker)
            if error_kind == "remote_api_error":
                failed.append(ticker)
                continue
            completed.append(ticker)
            if rating is not None:
                ratings.append(rating)
    finally:
        await browser.close()

    return ratings, completed, failed


def run(tickers: List[str], *, headless: bool = True) -> Tuple[List[TickerRating], List[str], List[str]]:
    return asyncio.get_event_loop().run_until_complete(run_async(tickers, headless=headless))


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ticker", action="append", dest="tickers", default=[])
    parser.add_argument("--tickers-file", default=None)
    parser.add_argument("--format", choices=["json", "csv"], default="json")
    parser.add_argument("--out", default="tipranks_results.json")
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument("--headed", action="store_true")
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
        ratings, completed, failed = run(tickers, headless=not args.headed)
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
