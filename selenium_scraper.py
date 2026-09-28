#!/usr/bin/env python3
"""selenium_scraper.py — Selenium engine for tipranks-scraper. Same CLI
flags, exit codes, and `TickerRating` schema as `playwright_scraper.py`
(CLAUDE.md §4); see that file's module docstring for the shared flow and
for the 2026-09-28 toolkit addition's own rationale (why: a real
Cloudflare block, not a confirmed CAPTCHA widget — see that file and
`captcha_solver.py`'s module docstrings for the honesty caveat this
engine inherits unchanged).

**Named per-engine limitation** (predates the 2026-09-28 toolkit add):
chromedriver's `driver.get()` does not expose the navigated response's
HTTP status code the way Playwright's `page.goto()` / pyppeteer's
`page.goto()` do. Rather than reach for `execute_cdp_cmd`'s lower-level
Network domain (real complexity for a single status code), this engine
detects a not-found page the same way a human would: the confirmed real
page title for an unknown ticker is literally `"Error 404: Page Not
Found - TipRanks.com"` (see `tipranks_parser.py`'s module docstring), so
`fetch_one()` below checks `driver.title` for that string. This is a
real, confirmed string, not a guess — but it is a different detection
mechanism from the other two engines, and is called out here rather than
left for a user to discover.

The same status-code blindness applies to the "blocked" (403/429) signal
`playwright_scraper.py` / `puppeteer_scraper.py` check at `page.goto()`
time: this engine can only see a 403/429 on the *in-page* `fetch()` call
against `payload.json`, not on the initial page navigation itself. Since
2026-09-28 this gap is narrowed (not closed) by also checking
`driver.current_url` / `driver.page_source` against
`tipranks_parser.BLOCK_URL_MARKERS` / `BLOCK_BODY_MARKERS` right after
navigation — the same real Cloudflare markers the other two engines check
via their own status+body reads — so the one real block this project has
actually observed is caught here too, even though a *hypothetical*
differently-shaped block that returns neither a checked marker nor a
403/429 on the in-page fetch still would not be, same as before.

**Selenium's two standing CLAUDE.md §6 limits, same as every sibling
repo**:

  - `--cdp-endpoint` is refused outright (`EXIT_BAD_USAGE`) when it
    carries credentials — chromedriver's `debuggerAddress` takes a bare
    `host:port` and cannot authenticate a WebSocket upgrade the way
    Playwright's `connect_over_cdp` / pyppeteer's `connect` do. This is
    exactly the shape of 2Captcha's own Scraping Browser API connection
    string, so in practice this means: use `playwright_scraper.py` or
    `puppeteer_scraper.py` for that product. A *bare*, uncredentialed
    `--cdp-endpoint` (local remote-debugging, not a 2Captcha session)
    still works here.
  - `--proxy`'s login/password cannot be attached to Selenium's
    `--proxy-server` Chrome flag at all (no authenticated-proxy API on
    this engine, unlike Playwright's `proxy=` dict or Puppeteer's
    `page.authenticate()`). Credentials are stripped before being handed
    to Chrome and this engine WARNS rather than silently dropping them.

Selenium Manager's own anonymous usage-stat phone-home
(`SE_AVOID_STATS`) is disabled by default below — see SECURITY.md.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from urllib.parse import urlparse

os.environ.setdefault("SE_AVOID_STATS", "true")

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
except ImportError:  # pragma: no cover - exercised by smoke_test.py with no engine installed
    webdriver = None
    Options = None

from captcha_solver import CaptchaType, build_injection_script, solve_when_blocked
from env_config import apply_env
from fingerprint_client import fetch_fingerprint, refuse_if_cdp, user_agent_from
from output_writer import EXIT_BAD_USAGE, EXIT_CRASH, finish_run
from proxy_pool import Proxy, ProxyParseError, ProxyPool, load_proxies, redact_credentials
from scraper_api_client import TwoCaptchaAuthError, TwoCaptchaClient, TwoCaptchaError
from tipranks_parser import (
    BLOCK_BODY_MARKERS,
    BLOCK_URL_MARKERS,
    is_allowed,
    normalize_ticker,
    parse_payload,
    ticker_page_url,
    ticker_payload_url,
    TickerRating,
)

log = logging.getLogger("tipranks.selenium")

ENGINE_NAME = "selenium"

# --- site-specific engine constants (CLAUDE.md §5) ---
NAV_TIMEOUT_S = 30
READINESS_WAIT_S = 2  # the SPA's own payload fetch happens right after load

NOT_FOUND_TITLE_MARKER = "Error 404: Page Not Found"
_BLOCKED_STATUSES = (403, 429)
_NOT_FOUND_STATUSES = (404, 400)

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


def _cdp_endpoint_has_credentials(cdp_endpoint: str) -> bool:
    parts = urlparse(cdp_endpoint)
    return bool(parts.username or parts.password)


def fetch_one(driver, ticker: str) -> Tuple[Optional[TickerRating], Optional[str]]:
    """Same contract as playwright_scraper.fetch_one: (rating_or_None,
    error_kind), never raises. See this module's own docstring for why
    the block-detection order here (title, then URL/body markers, then
    the in-page fetch's own __status) differs from the other two engines."""
    page_url = ticker_page_url(ticker)
    payload_url = ticker_payload_url(ticker)

    try:
        driver.set_page_load_timeout(NAV_TIMEOUT_S)
        driver.get(page_url)
    except Exception:
        return None, "remote_api_error"

    if NOT_FOUND_TITLE_MARKER in (driver.title or ""):
        return None, "not_found"

    try:
        current_url = driver.current_url
    except Exception:
        current_url = page_url
    if any(m in (current_url or "").lower() for m in BLOCK_URL_MARKERS):
        return None, "blocked"

    try:
        html = driver.page_source
    except Exception:
        html = ""
    if any(m in html.lower() for m in BLOCK_BODY_MARKERS):
        return None, "blocked"

    time.sleep(READINESS_WAIT_S)

    try:
        driver.set_script_timeout(NAV_TIMEOUT_S)
        payload = driver.execute_async_script(_FETCH_ASYNC_SCRIPT, payload_url)
    except Exception:
        return None, "remote_api_error"

    if isinstance(payload, dict) and payload.get("__status") in _BLOCKED_STATUSES:
        return None, "blocked"
    if isinstance(payload, dict) and payload.get("__status") in _NOT_FOUND_STATUSES:
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


def _fetch_one_via_scraper_api(
    ticker: str, *, client: TwoCaptchaClient, timeout: int, cdp_url: Optional[str],
) -> Tuple[Optional[TickerRating], Optional[str]]:
    """Identical logic to playwright_scraper.py's own copy — duplicated
    per engine per this family's own convention (CLAUDE.md §4), since
    this mode uses no browser/driver at all and has nothing engine-
    specific to say. See that function's docstring for the untested
    assumption this rests on (whether `payload.json` answers a fresh,
    cookie-less request the same way it answers the in-page authenticated
    fetch `fetch_one()` above uses)."""
    try:
        result = client.scrape_url(ticker_payload_url(ticker), timeout=timeout, cdp_url=cdp_url)
    except TwoCaptchaAuthError as exc:
        log.error("Scraper API: %s", exc)
        return None, "remote_api_error"
    except TwoCaptchaError as exc:
        log.error("Scraper API request failed — treating as remote_api_error, not a crash: %s", exc)
        return None, "remote_api_error"

    if result.target_status in _BLOCKED_STATUSES or any(
        m in (result.body or "").lower() for m in BLOCK_BODY_MARKERS
    ):
        return None, "blocked"
    if result.target_status in _NOT_FOUND_STATUSES:
        return None, "not_found"

    try:
        payload = json.loads(result.body) if result.body else None
    except ValueError:
        return None, "remote_api_error"
    if not isinstance(payload, dict) or "models" not in payload:
        return None, "remote_api_error"

    try:
        rating = parse_payload(
            payload, ticker, source_url=ticker_page_url(ticker), scraped_at=_now_iso()
        )
    except (TypeError, ValueError, AttributeError):
        return None, "remote_api_error"
    if rating is None:
        return None, "not_found"
    return rating, None


def _maybe_solve_captcha(
    *, client: Optional[TwoCaptchaClient], driver, ticker: str, policy: str, min_score: float,
) -> None:
    """Selenium equivalent of playwright_scraper.py's own helper — same
    "called only from an already-confirmed blocked outcome, no-op is
    expected" contract, see that function's docstring. Injection uses
    `driver.execute_script` in place of `page.evaluate`; there is no
    Selenium equivalent of `_enable_scraping_browser_auto_solve` because
    a credentialed `--cdp-endpoint` (the only case that would arm one) is
    refused outright on this engine (see module docstring)."""
    if client is None or policy == "off":
        return
    try:
        html = driver.page_source
        url = driver.current_url
    except Exception:
        return
    result = solve_when_blocked(
        client=client, page_url=url, html=html,
        count_product_links=lambda _h: 0,
        extra_markers=BLOCK_BODY_MARKERS, min_score=min_score,
    )
    action = result.get("action")
    if action == "solved":
        log.info("Captcha solved for %s (%s) — injecting and will retry.", ticker, result.get("captcha_type"))
        script = build_injection_script(CaptchaType(result["captcha_type"]), result["token"])
        if script:
            try:
                driver.execute_script(f"return {script}")
            except Exception as exc:
                log.warning("Captcha injection script failed for %s: %s", ticker, exc)
    elif action in ("warning_no_key", "warning_solver_error"):
        log.warning("Captcha solve attempt for %s: %s (%s)", ticker, action, result.get("detail"))
    elif action == "detected_unidentified_widget":
        log.info(
            "%s: a bot-challenge marker matched but no known solvable widget was found — "
            "expected here (see captcha_solver.py's module docstring), not a bug.",
            ticker,
        )


def _make_driver(
    headless: bool, executable_path: Optional[str], *,
    proxy: Optional[Proxy] = None, cdp_endpoint: Optional[str] = None,
    user_agent: Optional[str] = None,
):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    if executable_path:
        # The browser binary (Chrome/Chromium itself), not chromedriver —
        # Selenium calls that `binary_location`, unlike Playwright's/
        # pyppeteer's `executable_path`/`executablePath` for the same
        # concept. Kept for CLI parity with the other two engines
        # (CLAUDE.md §4).
        options.binary_location = executable_path
    if user_agent:
        options.add_argument(f"--user-agent={user_agent}")

    if cdp_endpoint:
        # Credential shape already refused before this is ever called
        # (see main()) — this is a bare host:port remote-debugging
        # target, no auth to attach.
        options.debugger_address = urlparse(cdp_endpoint).netloc.split("@")[-1]
        return webdriver.Chrome(options=options)

    if proxy is not None:
        if proxy.has_auth:
            log.warning(
                "Selenium's --proxy-server cannot authenticate — using %s:%s "
                "with credentials STRIPPED, not silently dropped.",
                proxy.host, proxy.port,
            )
        options.add_argument(f"--proxy-server={proxy.server_only()}")

    return webdriver.Chrome(options=options)


def run(
    tickers: List[str], *,
    headless: bool = True,
    executable_path: Optional[str] = None,
    proxy_pool: Optional[ProxyPool] = None,
    cdp_endpoint: Optional[str] = None,
    user_agent: Optional[str] = None,
    client: Optional[TwoCaptchaClient] = None,
    solve_captcha_policy: str = "when-blocked",
    min_score: float = 0.3,
    block_retries: int = 0,
) -> Tuple[List[TickerRating], List[str], List[str], List[str]]:
    """Returns (ratings, tickers_completed, failed_tickers,
    blocked_tickers) — same contract as playwright_scraper.run(). See
    that function's own docstring for the "one proxy for the whole run,
    no mid-run rotation" scoping decision, which applies unchanged here."""
    if webdriver is None:
        raise RuntimeError("selenium is not installed")

    ratings: List[TickerRating] = []
    completed: List[str] = []
    failed: List[str] = []
    blocked: List[str] = []

    proxy = proxy_pool.next() if proxy_pool else None
    try:
        driver = _make_driver(
            headless, executable_path, proxy=proxy, cdp_endpoint=cdp_endpoint, user_agent=user_agent,
        )
    except Exception as exc:
        log.error(
            "Driver/CDP connection failed — treating as remote_api_error, not a crash: %s",
            redact_credentials(str(exc)),
        )
        return ratings, completed, list(tickers), blocked

    try:
        for ticker in tickers:
            rating = None
            error_kind = None
            for attempt in range(block_retries + 1):
                rating, error_kind = fetch_one(driver, ticker)
                if error_kind != "blocked":
                    break
                if client is not None:
                    _maybe_solve_captcha(
                        client=client, driver=driver, ticker=ticker,
                        policy=solve_captcha_policy, min_score=min_score,
                    )
                if attempt < block_retries:
                    log.warning(
                        "Blocked fetching %s (attempt %d/%d) — retrying on the same session.",
                        ticker, attempt + 1, block_retries + 1,
                    )
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
        driver.quit()

    return ratings, completed, failed, blocked


def _run_via_scraper_api(
    tickers: List[str], *, client: TwoCaptchaClient, timeout: int, cdp_url: Optional[str],
    block_retries: int,
) -> Tuple[List[TickerRating], List[str], List[str], List[str]]:
    """Identical to playwright_scraper.py's own copy — no browser/driver
    concepts apply in this mode."""
    ratings: List[TickerRating] = []
    completed: List[str] = []
    failed: List[str] = []
    blocked: List[str] = []

    for ticker in tickers:
        rating = error_kind = None
        for attempt in range(block_retries + 1):
            rating, error_kind = _fetch_one_via_scraper_api(ticker, client=client, timeout=timeout, cdp_url=cdp_url)
            if error_kind != "blocked":
                break
            if attempt < block_retries:
                log.warning(
                    "Blocked fetching %s via Scraper API (attempt %d/%d) — retrying.",
                    ticker, attempt + 1, block_retries + 1,
                )
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

    return ratings, completed, failed, blocked


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
    parser.add_argument(
        "--block-retries", type=int, default=2,
        help="On a blocked outcome, retry that ticker on the SAME browser/session (same exit IP, "
             "same device identity) this many extra times before giving up — 'retry before you "
             "rotate', not a proxy/session swap. Default 2, matching the rest of the family.",
    )
    parser.add_argument("--proxy", default=None, help="A single proxy, e.g. http://login:pass@host:port (or set TIPRANKS_PROXY). Selenium's --proxy-server cannot authenticate — credentials are stripped, with a warning.")
    parser.add_argument("--proxy-file", default=None, help="One proxy per line, same formats as --proxy")
    parser.add_argument("--proxy-shuffle", action="store_true")
    parser.add_argument("--twocaptcha-key", default=None, help="(or set TWOCAPTCHA_KEY)")
    parser.add_argument("--captcha-api", default=None, help="Override the 2Captcha API base URL (testing only)")
    parser.add_argument(
        "--solve-captcha", choices=["off", "when-blocked", "always"], default="when-blocked",
        help="'always' is accepted for CLI parity with every sibling repo, but behaves the same "
             "as 'when-blocked' here — this repo has no 'products already present' state a captcha "
             "solve should be skipped for (a ticker fetch is either blocked or it isn't).",
    )
    parser.add_argument("--min-score", type=float, default=0.3, help="Minimum acceptable reCAPTCHA v3 score (2Captcha's minScore task field)")
    parser.add_argument(
        "--cdp-endpoint", default=None,
        help="Connect to a remote CDP session instead of launching locally (or set TIPRANKS_CDP_ENDPOINT). "
             "REFUSED (EXIT_BAD_USAGE) if it carries credentials — chromedriver cannot authenticate a "
             "remote CDP session (unlike Playwright/Puppeteer); use playwright_scraper.py or "
             "puppeteer_scraper.py for the 2Captcha Scraping Browser API.",
    )
    parser.add_argument("--fingerprint", action="store_true", help="Fetch and apply a 2Captcha Fingerprint API profile (ignored with --cdp-endpoint — see fingerprint_client.refuse_if_cdp)")
    parser.add_argument("--fp-tags", default=None, help="Fingerprint API filter, e.g. 'Windows,Chrome'")
    parser.add_argument("--fp-country", default=None, help="Fingerprint API filter, e.g. 'us'")
    parser.add_argument(
        "--scraper-api", action="store_true",
        help="Fetch via 2Captcha's Scraper API (scraper.2captcha.com) instead of launching any local "
             "or --cdp-endpoint browser. Requires --twocaptcha-key/TWOCAPTCHA_KEY. Unlike sibling "
             "repos, this fetches ticker_payload_url() directly (the JSON endpoint), not the "
             "rendered page — see playwright_scraper.py's _fetch_one_via_scraper_api() docstring for "
             "the untested assumption this rests on. --headed/--executable-path/--proxy/"
             "--cdp-endpoint/--fingerprint are all IGNORED in this mode (a single static fetch per "
             "ticker has no browser session, and brings its own exit IP/device) — set together, "
             "they log a warning rather than silently doing nothing.",
    )
    parser.add_argument("--scraper-api-timeout", type=int, default=60, help="Seconds 2Captcha itself waits for the target page to finish loading (1-120, their limit)")
    parser.add_argument("--scraper-api-url", default=None, help="Override the Scraper API base URL (testing only)")
    parser.add_argument(
        "--scraper-api-cdp", action="store_true",
        help="Route --scraper-api's fetch through a 2Captcha Scraping Browser CDP session (their "
             "'cdpurl' field on the Scraper API task) instead of their own default pool — chaining "
             "two 2Captcha products together, not a caller-supplied --cdp-endpoint (that flag stays "
             "ignored in --scraper-api mode). Requires --scraper-api. WIRED BUT NOT YET LIVE-TESTED "
             "— see scraper_api_client.TwoCaptchaClient.scraping_browser_connection_url/scrape_url's "
             "own docstrings and TESTING.md.",
    )
    parser.add_argument("--scraper-api-country", default=None, help="Exit country for --scraper-api-cdp's Scraping Browser session, e.g. 'us' (ignored without --scraper-api-cdp)")
    parser.add_argument("--scraper-api-profile-id", default=None, help="Reuse a specific Scraping Browser profile id across runs for --scraper-api-cdp, instead of the default pool (ignored without --scraper-api-cdp)")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    apply_env(args)

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

    if args.scraper_api_cdp and not args.scraper_api:
        print("error: --scraper-api-cdp requires --scraper-api", file=sys.stderr)
        return EXIT_BAD_USAGE

    started_at = time.time()

    if args.scraper_api:
        if not args.twocaptcha_key:
            print("error: --scraper-api requires --twocaptcha-key/TWOCAPTCHA_KEY", file=sys.stderr)
            return EXIT_BAD_USAGE
        if args.headed or args.executable_path or args.proxy or args.proxy_file or args.cdp_endpoint or args.fingerprint:
            log.warning(
                "--scraper-api ignores --headed/--executable-path/--proxy/--proxy-file/"
                "--cdp-endpoint/--fingerprint — this mode brings its own exit IP/device via "
                "2Captcha's own infrastructure, see --scraper-api's help text."
            )
        if (args.scraper_api_country or args.scraper_api_profile_id) and not args.scraper_api_cdp:
            log.warning(
                "--scraper-api-country/--scraper-api-profile-id are ignored without "
                "--scraper-api-cdp — there is no Scraping Browser session for them to apply to."
            )
        client = TwoCaptchaClient(args.twocaptcha_key, api_base=args.captcha_api, scraper_api_base=args.scraper_api_url)
        cdp_url = None
        if args.scraper_api_cdp:
            cdp_url = client.scraping_browser_connection_url(
                country=args.scraper_api_country, profile_id=args.scraper_api_profile_id,
            )
        try:
            ratings, completed, failed, blocked_tickers = _run_via_scraper_api(
                tickers, client=client, timeout=args.scraper_api_timeout, cdp_url=cdp_url,
                block_retries=args.block_retries,
            )
        except Exception as exc:
            print(f"crash: {exc}", file=sys.stderr)
            return EXIT_CRASH
    else:
        if args.cdp_endpoint and _cdp_endpoint_has_credentials(args.cdp_endpoint):
            print(
                "error: --cdp-endpoint carries credentials — chromedriver's debuggerAddress takes a "
                "bare host:port and cannot authenticate a remote session. Use playwright_scraper.py "
                "or puppeteer_scraper.py for the 2Captcha Scraping Browser API.", file=sys.stderr,
            )
            return EXIT_BAD_USAGE
        # No `webdriver is None` guard here on purpose: run() already
        # raises RuntimeError("selenium is not installed") for that case
        # (see below), and the try/except around the run() call below
        # already turns that into EXIT_CRASH. A separate pre-check here
        # would run unconditionally, BEFORE run() is even called — which
        # would also fire when a test (smoke_test.py's fake_run_* checks)
        # monkeypatches `run` itself to a fake that doesn't need a real
        # driver at all, breaking the same monkeypatch pattern playwright_
        # scraper.py/puppeteer_scraper.py already rely on. Confirmed live:
        # this exact duplicate check was here briefly and only looked
        # correct because this developer's own sandbox happened to have
        # `selenium` installed; on a real machine without it, every
        # fake-run smoke_test.py check for this engine failed with
        # EXIT_CRASH instead of exercising the fake at all.

        proxies = []
        try:
            proxies = load_proxies(args.proxy, args.proxy_file)
        except ProxyParseError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_BAD_USAGE
        pool = ProxyPool(proxies, shuffle=args.proxy_shuffle) if proxies else None
        if args.cdp_endpoint and pool is not None:
            log.warning("Ignoring --proxy: a --cdp-endpoint session already carries its own exit IP.")
            pool = None

        captcha_client = None
        if args.twocaptcha_key and (args.solve_captcha != "off" or args.fingerprint):
            captcha_client = TwoCaptchaClient(args.twocaptcha_key, api_base=args.captcha_api)

        user_agent = None
        cdp_refused_fingerprint = refuse_if_cdp(args.cdp_endpoint)
        if args.fingerprint and not cdp_refused_fingerprint:
            if captcha_client is None:
                log.warning("--fingerprint requested but no --twocaptcha-key/TWOCAPTCHA_KEY set — continuing without one.")
            else:
                profile = fetch_fingerprint(captcha_client, tags=args.fp_tags, country=args.fp_country)
                if profile:
                    user_agent = user_agent_from(profile)

        try:
            ratings, completed, failed, blocked_tickers = run(
                tickers,
                headless=not args.headed,
                executable_path=args.executable_path,
                proxy_pool=pool,
                cdp_endpoint=args.cdp_endpoint,
                user_agent=user_agent,
                client=captcha_client,
                solve_captcha_policy=args.solve_captcha,
                min_score=args.min_score,
                block_retries=args.block_retries,
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
