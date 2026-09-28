#!/usr/bin/env python3
"""puppeteer_scraper.py — Puppeteer (via pyppeteer) engine for
tipranks-scraper. Same CLI flags, exit codes, and `TickerRating` schema
as `playwright_scraper.py` (CLAUDE.md §4); see that file's module
docstring for the shared flow and for the 2026-09-28 toolkit addition's
own rationale (why: a real Cloudflare block, not a confirmed CAPTCHA
widget — see that file and `captcha_solver.py`'s module docstrings for
the honesty caveat this engine inherits unchanged). Unlike Selenium,
pyppeteer's `page.goto()` returns a real response object with `.status`,
so not-found/blocked detection here matches Playwright's (a real HTTP
status), not the title-string fallback `selenium_scraper.py` needs — and,
like Playwright (unlike Selenium), this engine CAN open an authenticated
`ws://login:pass@host:port` CDP session, so it is the second engine able
to use the Scraping Browser API's own `Captcha.setAutoSolve` (CLAUDE.md
§6's named Selenium exception does not apply here).

**Named per-engine limitation, confirmed live, not a guess** (predates
the 2026-09-28 toolkit add): pyppeteer (unmaintained upstream) downloads
its own pinned Chromium build the first time it launches — confirmed to
resolve to revision 117.0.5938.0. On a real Apple Silicon Mac (macOS,
arm64) that exact binary launches (it answers `--version`) but then
segfaults (`SIGSEGV`, a `mach_port_rendezvous` XPC error in its log) on
an actual headless run — reproduced directly, outside pyppeteer, by
invoking the binary itself, not just inferred from pyppeteer's own
unhelpful `"Browser closed unexpectedly"` message. Playwright's and
Selenium's own browser/driver downloads were not affected on the same
machine, so this is specific to pyppeteer's old pinned revision, not this
site or this repo's code. The practical fix is `--executable-path`,
below: point this engine at an already-installed, working
Chrome/Chromium instead of pyppeteer's own broken bundled one.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

try:
    from pyppeteer import connect as pyppeteer_connect
    from pyppeteer import launch as pyppeteer_launch
except ImportError:  # pragma: no cover - exercised by smoke_test.py with no engine installed
    pyppeteer_launch = None
    pyppeteer_connect = None

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

log = logging.getLogger("tipranks.puppeteer")

ENGINE_NAME = "puppeteer"

# --- site-specific engine constants (CLAUDE.md §5) ---
NAV_TIMEOUT_MS = 30_000
READINESS_WAIT_MS = 2_000  # the SPA's own payload fetch happens right after load

_BLOCKED_STATUSES = (403, 429)
_NOT_FOUND_STATUSES = (404, 400)

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


async def _launch(
    *, headless: bool, executable_path: Optional[str], proxy: Optional[Proxy], cdp_endpoint: Optional[str],
):
    if cdp_endpoint:
        try:
            return await pyppeteer_connect(browserWSEndpoint=cdp_endpoint, defaultViewport=None)
        except Exception as exc:
            raise RuntimeError(f"CDP connection failed: {redact_credentials(str(exc))}") from None
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    if proxy is not None:
        args.append(proxy.pyppeteer_launch_arg())
    kwargs = dict(headless=headless, args=args)
    if executable_path:
        # Bypass pyppeteer's own bundled Chromium download entirely (see
        # module docstring: that pinned revision is confirmed to segfault
        # on at least one real machine) in favor of an already-installed,
        # working Chrome/Chromium.
        kwargs["executablePath"] = executable_path
    return await pyppeteer_launch(**kwargs)


async def _authenticate_if_needed(page, proxy: Optional[Proxy]) -> None:
    if proxy is not None:
        auth = proxy.pyppeteer_auth_dict()
        if auth:
            await page.authenticate(auth)


async def _enable_scraping_browser_auto_solve(page) -> None:
    """Only meaningful over --cdp-endpoint — see the identical helper in
    every sibling repo's own engine file. Never fatal — an
    unsupported/unavailable CDP domain just means no auto-solve, not a
    crashed run."""
    try:
        client = await page.target.createCDPSession()
        client.on("Captcha.detected", lambda *_: log.info("[Scraping Browser API] captcha detected"))
        client.on("Captcha.solveFinished", lambda *_: log.info("[Scraping Browser API] captcha solved"))
        client.on("Captcha.solveFailed", lambda *_: log.warning("[Scraping Browser API] captcha solve failed"))
        await client.send("Captcha.setAutoSolve", {"autoSolve": True, "options": [{"type": "*"}]})
    except Exception as exc:  # noqa: BLE001 — optional enhancement, never fatal
        log.warning("Captcha.setAutoSolve unavailable on this CDP session (continuing without it): %s", exc)


def _looks_blocked(status: Optional[int], url: str, html: str) -> bool:
    if status in _BLOCKED_STATUSES:
        return True
    if any(marker in (url or "").lower() for marker in BLOCK_URL_MARKERS):
        return True
    if any(marker in (html or "").lower() for marker in BLOCK_BODY_MARKERS):
        return True
    return False


async def fetch_one_async(page, ticker: str) -> Tuple[Optional[TickerRating], Optional[str]]:
    """Same contract as playwright_scraper.fetch_one: (rating_or_None,
    error_kind), never raises."""
    page_url = ticker_page_url(ticker)
    payload_url = ticker_payload_url(ticker)

    try:
        response = await page.goto(page_url, {"waitUntil": "domcontentloaded", "timeout": NAV_TIMEOUT_MS})
    except Exception:
        return None, "remote_api_error"

    status = response.status if response is not None else None
    if status in _NOT_FOUND_STATUSES:
        return None, "not_found"
    if status in _BLOCKED_STATUSES or any(m in (page.url or "").lower() for m in BLOCK_URL_MARKERS):
        return None, "blocked"
    if status != 200:
        try:
            html = await page.content()
        except Exception:
            html = ""
        if any(m in html.lower() for m in BLOCK_BODY_MARKERS):
            return None, "blocked"

    await asyncio.sleep(READINESS_WAIT_MS / 1000)

    try:
        payload = await page.evaluate(_FETCH_SCRIPT, payload_url)
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
    per engine per this family's own convention (CLAUDE.md §4). A plain
    `requests` call (via TwoCaptchaClient.scrape_url), not pyppeteer —
    nothing here needs `await`. See that function's docstring for the
    untested assumption this rests on (whether `payload.json` answers a
    fresh, cookie-less request the same way it answers the in-page
    authenticated fetch `fetch_one_async()` above uses)."""
    try:
        result = client.scrape_url(ticker_payload_url(ticker), timeout=timeout, cdp_url=cdp_url)
    except TwoCaptchaAuthError as exc:
        log.error("Scraper API: %s", exc)
        return None, "remote_api_error"
    except TwoCaptchaError as exc:
        log.error("Scraper API request failed — treating as remote_api_error, not a crash: %s", exc)
        return None, "remote_api_error"

    if _looks_blocked(result.target_status, "", result.body):
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


async def _maybe_solve_captcha(
    *, client: Optional[TwoCaptchaClient], page, ticker: str, policy: str, min_score: float,
) -> None:
    """Async equivalent of playwright_scraper.py's own helper — same
    "called only from an already-confirmed blocked outcome, no-op is
    expected" contract, see that function's docstring."""
    if client is None or policy == "off":
        return
    try:
        html = await page.content()
    except Exception:
        return
    result = solve_when_blocked(
        client=client, page_url=page.url, html=html,
        count_product_links=lambda _h: 0,
        extra_markers=BLOCK_BODY_MARKERS, min_score=min_score,
    )
    action = result.get("action")
    if action == "solved":
        log.info("Captcha solved for %s (%s) — injecting and will retry.", ticker, result.get("captcha_type"))
        script = build_injection_script(CaptchaType(result["captcha_type"]), result["token"])
        if script:
            try:
                await page.evaluate(script)
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


async def run_async(
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
    if pyppeteer_launch is None:
        raise RuntimeError("pyppeteer is not installed")

    ratings: List[TickerRating] = []
    completed: List[str] = []
    failed: List[str] = []
    blocked: List[str] = []

    proxy = proxy_pool.next() if proxy_pool else None
    try:
        browser = await _launch(
            headless=headless, executable_path=executable_path, proxy=proxy, cdp_endpoint=cdp_endpoint,
        )
    except RuntimeError as exc:
        log.error("CDP connection failed — treating as remote_api_error, not a crash: %s", exc)
        return ratings, completed, list(tickers), blocked

    try:
        page = await browser.newPage()
        if user_agent:
            await page.setUserAgent(user_agent)
        await _authenticate_if_needed(page, proxy)
        if cdp_endpoint:
            await _enable_scraping_browser_auto_solve(page)

        for ticker in tickers:
            rating = None
            error_kind = None
            for attempt in range(block_retries + 1):
                rating, error_kind = await fetch_one_async(page, ticker)
                if error_kind != "blocked":
                    break
                # Over --cdp-endpoint, 2Captcha's own auto-solve (armed
                # above) already had its chance inside fetch_one_async()'s
                # own navigation — a second, LOCAL solve attempt here
                # would be redundant (captcha_solver.py's own module
                # docstring).
                if client is not None and not cdp_endpoint:
                    await _maybe_solve_captcha(
                        client=client, page=page, ticker=ticker,
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
        await browser.close()

    return ratings, completed, failed, blocked


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
    return asyncio.get_event_loop().run_until_complete(
        run_async(
            tickers, headless=headless, executable_path=executable_path,
            proxy_pool=proxy_pool, cdp_endpoint=cdp_endpoint, user_agent=user_agent,
            client=client, solve_captcha_policy=solve_captcha_policy, min_score=min_score,
            block_retries=block_retries,
        )
    )


def _run_via_scraper_api(
    tickers: List[str], *, client: TwoCaptchaClient, timeout: int, cdp_url: Optional[str],
    block_retries: int,
) -> Tuple[List[TickerRating], List[str], List[str], List[str]]:
    """Identical to playwright_scraper.py's own copy — no browser
    concepts apply in this mode, plain synchronous calls."""
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
        help="Use an already-installed Chrome/Chromium instead of this engine's own "
        "bundled-browser download (see module docstring for why puppeteer_scraper.py "
        "in particular may need this).",
    )
    parser.add_argument(
        "--block-retries", type=int, default=2,
        help="On a blocked outcome, retry that ticker on the SAME browser/session (same exit IP, "
             "same device identity) this many extra times before giving up — 'retry before you "
             "rotate', not a proxy/session swap. Default 2, matching the rest of the family.",
    )
    parser.add_argument("--proxy", default=None, help="A single proxy, e.g. http://login:pass@host:port (or set TIPRANKS_PROXY)")
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
    parser.add_argument("--cdp-endpoint", default=None, help="Connect to a remote CDP session (e.g. the 2Captcha Scraping Browser API) instead of launching locally (or set TIPRANKS_CDP_ENDPOINT) — opt-in, not required for a normal run")
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
        if pyppeteer_launch is None:
            print("error: pyppeteer is not installed", file=sys.stderr)
            return EXIT_CRASH

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
