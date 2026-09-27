#!/usr/bin/env python3
"""puppeteer_scraper.py — Puppeteer (via pyppeteer) engine for
tipranks-scraper. Same CLI flags, exit codes, and `TickerRating` schema
as `playwright_scraper.py` (CLAUDE.md §4); see that file's module
docstring for the shared flow. Unlike Selenium, pyppeteer's `page.goto()`
returns a real response object with `.status`, so not-found detection
here matches Playwright's (a real HTTP 404/400), not the title-string
fallback `selenium_scraper.py` needs.

**Named per-engine limitation, confirmed live, not a guess**: pyppeteer
(unmaintained upstream) downloads its own pinned Chromium build the first
time it launches — confirmed to resolve to revision 117.0.5938.0. On a
real Apple Silicon Mac (macOS, arm64) that exact binary launches (it
answers `--version`) but then segfaults (`SIGSEGV`, a
`mach_port_rendezvous` XPC error in its log) on an actual headless run —
reproduced directly, outside pyppeteer, by invoking the binary itself,
not just inferred from pyppeteer's own unhelpful `"Browser closed
unexpectedly"` message. Playwright's and Selenium's own browser/driver
downloads were not affected on the same machine, so this is specific to
pyppeteer's old pinned revision, not this site or this repo's code. The
practical fix is `--executable-path`, below: point this engine at an
already-installed, working Chrome/Chromium instead of pyppeteer's own
broken bundled one.
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

from output_writer import EXIT_BAD_USAGE, EXIT_CRASH, finish_run
from tipranks_parser import (
    is_allowed,
    normalize_ticker,
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
    error_kind), never raises. error_kind "blocked" (403/429) is checked
    before "not_found" (404/400), same statuses playwright_scraper.py
    checks for the same reason — see that file's module comment."""
    page_url = ticker_page_url(ticker)
    payload_url = ticker_payload_url(ticker)

    try:
        response = await page.goto(page_url, timeout=NAV_TIMEOUT_MS, waitUntil="domcontentloaded")
    except Exception:
        return None, "remote_api_error"

    if response is not None and response.status in (403, 429):
        return None, "blocked"
    if response is not None and response.status in (404, 400):
        return None, "not_found"

    await asyncio.sleep(READINESS_WAIT_MS / 1000)

    try:
        payload = await page.evaluate(_FETCH_SCRIPT, payload_url)
    except Exception:
        return None, "remote_api_error"

    if isinstance(payload, dict) and payload.get("__status") in (403, 429):
        return None, "blocked"
    if isinstance(payload, dict) and payload.get("__status") in (404, 400):
        return None, "not_found"
    if not isinstance(payload, dict) or "models" not in payload:
        return None, "remote_api_error"

    try:
        rating = parse_payload(payload, ticker, source_url=page_url, scraped_at=_now_iso())
    except (TypeError, ValueError, AttributeError):
        return None, "remote_api_error"
    if rating is None:
        return None, "not_found"
    return rating, None


async def run_async(
    tickers: List[str], *, headless: bool = True, executable_path: Optional[str] = None
) -> Tuple[List[TickerRating], List[str], List[str], List[str]]:
    """Returns (ratings, tickers_completed, failed_tickers,
    blocked_tickers) — same contract as playwright_scraper.run()."""
    if pyppeteer_launch is None:
        raise RuntimeError("pyppeteer is not installed")

    ratings: List[TickerRating] = []
    completed: List[str] = []
    failed: List[str] = []
    blocked: List[str] = []

    launch_kwargs = {"headless": headless, "args": ["--no-sandbox"]}
    if executable_path:
        # Bypass pyppeteer's own bundled Chromium download entirely (see
        # module docstring: that pinned revision is confirmed to segfault
        # on at least one real machine) in favor of an already-installed,
        # working Chrome/Chromium.
        launch_kwargs["executablePath"] = executable_path
    browser = await pyppeteer_launch(**launch_kwargs)
    try:
        page = await browser.newPage()
        for ticker in tickers:
            rating, error_kind = await fetch_one_async(page, ticker)
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
    finally:
        await browser.close()

    return ratings, completed, failed, blocked


def run(
    tickers: List[str], *, headless: bool = True, executable_path: Optional[str] = None
) -> Tuple[List[TickerRating], List[str], List[str], List[str]]:
    return asyncio.get_event_loop().run_until_complete(
        run_async(tickers, headless=headless, executable_path=executable_path)
    )


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
        help="Use an already-installed Chrome/Chromium instead of this engine's own "
        "bundled-browser download (see module docstring for why puppeteer_scraper.py "
        "in particular may need this).",
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
        blocked=bool(blocked_tickers),
        remote_api_error=(
            bool(set(failed) - set(blocked_tickers)) and not completed
        ),
        allow_empty=args.allow_empty,
        started_at=started_at,
    )


if __name__ == "__main__":
    sys.exit(main())
