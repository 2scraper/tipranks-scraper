#!/usr/bin/env python3
"""playwright_scraper.py — primary (and, for now, only) engine for
tipranks-scraper. Scrapes one thing: a stock ticker's forecast-page
snapshot (Smart Score, analyst consensus, price target, Buy/Hold/Sell
rating) from tipranks.com's free, unauthenticated data.

Flow per ticker, confirmed live during research for this repo: navigate
to `https://www.tipranks.com/stocks/{ticker}/stock-analysis`, then fetch
the same-origin `.../stock-analysis/payload.json` from inside the page
(a same-origin `fetch()`, not a separate cookie-less HTTP client) and
parse the JSON. No DOM/CSS scraping is used for any field this repo
cares about.

**Added 2026-09-28**: `--proxy`/`--cdp-endpoint`/`--fingerprint`/
`--twocaptcha-key`/`--solve-captcha`/`--scraper-api(-cdp)` — the same
2Captcha-backed toolkit every other family member ships (CLAUDE.md §7),
copied in after a real Cloudflare block was observed in a pre-release
audit (CHANGELOG.md 0.2.0, `tipranks_parser.py`'s module docstring).
Unlike shein.com/g2.com, this repo has NEVER observed an actual CAPTCHA
widget — the one real block seen was a Cloudflare JS interstitial (HTTP
403, body "Just a moment...", a `__cf_chl_rt_tk` URL token), which a real
browser normally clears on its own rather than something with a solvable
widget. `--proxy` and `--cdp-endpoint` are this repo's own evidence-
backed mitigations (a fresh exit IP / a managed device identity);
`captcha_solver.py`'s detection/solving is family-standard plumbing that
may simply find nothing to solve here — see that module's own docstring
for the honesty caveat, and TESTING.md for what's actually confirmed.

The handful of engine constants allowed to vary per site (CLAUDE.md §5)
are directly below; nothing else here should need site-specific tuning.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - exercised by smoke_test.py with no engine installed
    sync_playwright = None

from captcha_solver import CaptchaType, build_injection_script, solve_when_blocked
from env_config import apply_env
from fingerprint_client import fetch_fingerprint, refuse_if_cdp, user_agent_from
from output_writer import EXIT_BAD_USAGE, EXIT_CRASH, finish_run
from proxy_pool import ProxyParseError, ProxyPool, load_proxies, redact_credentials
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

log = logging.getLogger("tipranks.playwright")

# --- site-specific engine constants (CLAUDE.md §5) ---
NAV_TIMEOUT_MS = 30_000
READINESS_WAIT_MS = 2_000  # the SPA's own payload fetch happens right after load

_BLOCKED_STATUSES = (403, 429)  # standard "you are blocked/rate-limited" statuses,
# and — since 2026-09-28 — the ONLY statuses this project has ever actually
# observed a real block return (see tipranks_parser.BLOCK_BODY_MARKERS'
# module comment).
_NOT_FOUND_STATUSES = (404, 400)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _enable_scraping_browser_auto_solve(context, page) -> None:
    """Only meaningful over --cdp-endpoint — see the identical helper in
    every sibling repo's playwright_scraper.py. Arms 2Captcha's own
    Captcha.setAutoSolve CDP domain, which solves AND injects entirely on
    THEIR side of the session — captcha_solver.py's local solve/inject
    path is skipped whenever this is in play (see that module's docstring
    on why the two are mutually exclusive, not additive). Never fatal — an
    unsupported/unavailable CDP domain just means no auto-solve, not a
    crashed run."""
    try:
        session = context.new_cdp_session(page)
        session.on("Captcha.detected", lambda *_: log.info("[Scraping Browser API] captcha detected"))
        session.on("Captcha.solveFinished", lambda *_: log.info("[Scraping Browser API] captcha solved"))
        session.on("Captcha.solveFailed", lambda *_: log.warning("[Scraping Browser API] captcha solve failed"))
        session.send("Captcha.setAutoSolve", {"autoSolve": True, "options": [{"type": "*"}]})
    except Exception as exc:  # noqa: BLE001 — optional enhancement, never fatal
        log.warning("Captcha.setAutoSolve unavailable on this CDP session (continuing without it): %s", exc)


def _looks_blocked(status: Optional[int], url: str, html: str) -> bool:
    """The HTTP-status check alone (the only signal this repo had before
    2026-09-28) OR'd with the two confirmed body/URL markers from the one
    real block ever observed here — catches the same challenge should it
    ever answer with a different status on a future capture, without
    inventing a generic challenge-detection heuristic this project has
    never seen fire (see tipranks_parser.py's module docstring: reCAPTCHA/
    Turnstile scripts are present in the page's own JS but have never
    actually rendered a challenge)."""
    if status in _BLOCKED_STATUSES:
        return True
    if any(marker in (url or "").lower() for marker in BLOCK_URL_MARKERS):
        return True
    if any(marker in (html or "").lower() for marker in BLOCK_BODY_MARKERS):
        return True
    return False


def fetch_one(page, ticker: str) -> Tuple[Optional[TickerRating], Optional[str]]:
    """Returns (rating_or_None, error_kind). error_kind is None on
    success, "not_found" for a real 404/400, "blocked" for a real 403/429
    or a confirmed body/URL block marker, or "remote_api_error" for
    anything else (nav timeout, non-JSON response, etc.) — never raises,
    so one bad ticker can't take a multi-ticker run down with it
    (CLAUDE.md §6). Captcha-solve/retry policy lives in run()'s per-ticker
    loop, not here — this stays a single fetch attempt, matching every
    other engine's fetch_one (CLAUDE.md §4)."""
    page_url = ticker_page_url(ticker)
    payload_url = ticker_payload_url(ticker)

    try:
        response = page.goto(page_url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
    except Exception:
        return None, "remote_api_error"

    status = response.status if response is not None else None
    if status in _NOT_FOUND_STATUSES:
        return None, "not_found"
    if status in _BLOCKED_STATUSES or any(m in (page.url or "").lower() for m in BLOCK_URL_MARKERS):
        return None, "blocked"
    if status != 200:
        # Only pay for reading the body when the status alone didn't
        # already answer the question — the happy path (a clean 200)
        # never calls page.content() at all, same cost as before this
        # change.
        try:
            html = page.content()
        except Exception:
            html = ""
        if any(m in html.lower() for m in BLOCK_BODY_MARKERS):
            return None, "blocked"

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


def _fetch_one_via_scraper_api(
    ticker: str, *, client: TwoCaptchaClient, timeout: int, cdp_url: Optional[str],
) -> Tuple[Optional[TickerRating], Optional[str]]:
    """--scraper-api's own fetch path for this repo: pointed at
    `ticker_payload_url()`, the raw JSON endpoint — NOT the rendered page,
    unlike shein-scraper/g2-scraper (which parse embedded state out of
    rendered HTML). This repo's parser needs `payload.json`'s JSON body
    directly, and there is no in-browser `page.evaluate()` step here for
    2Captcha's own headless browser to run for us (the Scraper API hands
    back a fetched document, it does not let a caller execute arbitrary
    JS and read the result — see scraper_api_client.py's module
    docstring). **Genuinely untested assumption, stated plainly**: the
    in-page fetch this repo's browser-based engines use sends
    `credentials: 'include'` (see fetch_one() above); whether
    `payload.json` also answers correctly to a completely FRESH, cookie-
    less request from 2Captcha's own infrastructure — the shape this call
    makes — has never been confirmed. `stock-analysis/payload.json`
    fetched separately did answer 200 in the one real block this repo
    observed (tipranks_parser.py's module docstring), which is suggestive
    but was still within the SAME browser session's cookies, not a fresh
    request. Confirm this live (TESTING.md) before relying on it."""
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


def _maybe_solve_captcha(
    *, client: Optional[TwoCaptchaClient], page, ticker: str, policy: str, min_score: float,
) -> None:
    """Called only from within an already-confirmed "blocked" outcome
    (CLAUDE.md §4-style parity with every sibling's own captcha hook), so
    `count_product_links` is unconditionally 0 — there is no "products
    already present" state to protect the way a listing page has.
    Injects a solved token in place and returns; the CALLER's own retry
    loop (run(), below) is what re-fetches to see whether it helped —
    this function never re-fetches itself. A no-op (nothing detected, no
    key, a solver error) is expected and logged, not treated as a
    failure — see this module's own docstring on why no captcha widget
    has ever actually been seen on this site."""
    if client is None or policy == "off":
        return
    try:
        html = page.content()
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
                page.evaluate(script)
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
    # "no_captcha_detected": the block marker check in run()'s caller
    # already established blocked=True before this was even called, so
    # this branch means captcha_solver's OWN broader marker list didn't
    # independently agree — logged at debug level only, nothing actionable.


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
    blocked_tickers) — see the original docstring (still accurate) for
    the exit-code implications. `executable_path`, if given, is passed
    straight to `chromium.launch()`.

    **Scoping decision, stated plainly**: unlike shein-scraper/g2-scraper
    (which rotate to a fresh proxy per scroll round), this repo picks ONE
    proxy for the WHOLE run (via `proxy_pool.next()`, once, respecting
    `--proxy-shuffle`) and does not rotate mid-run on failure. A ticker
    loop with one small JSON fetch per ticker doesn't carry the same
    per-page volume shein/g2's rotation was built for, and tearing down
    and relaunching the browser per ticker to support live rotation would
    be a lot of complexity for a benefit this repo has no evidence yet
    that it needs — `--proxy-file` still lets a caller pick a different
    proxy across separate invocations. Revisit if live testing shows
    otherwise (TESTING.md)."""
    if sync_playwright is None:
        raise RuntimeError("playwright is not installed")

    ratings: List[TickerRating] = []
    completed: List[str] = []
    failed: List[str] = []
    blocked: List[str] = []

    with sync_playwright() as pw:
        proxy = proxy_pool.next() if proxy_pool else None
        cdp_connect_failed = False
        if cdp_endpoint:
            try:
                browser = pw.chromium.connect_over_cdp(cdp_endpoint)
            except Exception as exc:
                log.error("CDP connection failed — treating as remote_api_error, not a crash: %s", redact_credentials(str(exc)))
                cdp_connect_failed = True
                browser = None
        else:
            launch_kwargs = {"headless": headless}
            if executable_path:
                launch_kwargs["executable_path"] = executable_path
            if proxy:
                launch_kwargs["proxy"] = proxy.playwright_proxy_dict()
                log.info("Using proxy %s", proxy.masked())
            browser = pw.chromium.launch(**launch_kwargs)

        if cdp_connect_failed:
            # Same convention every sibling repo uses: a broken remote
            # dependency is a remote_api_error for every requested ticker,
            # not a crash (CLAUDE.md §10) — main()'s own remote_api_error
            # derivation (bool(failed - blocked) and not completed) comes
            # out True automatically from this shape, with no separate
            # signal needed.
            return ratings, completed, list(tickers), blocked

        try:
            page = browser.new_page(user_agent=user_agent) if user_agent else browser.new_page()
            context = page.context
            if cdp_endpoint:
                _enable_scraping_browser_auto_solve(context, page)

            for ticker in tickers:
                rating = None
                error_kind = None
                for attempt in range(block_retries + 1):
                    rating, error_kind = fetch_one(page, ticker)
                    if error_kind != "blocked":
                        break
                    # Over --cdp-endpoint, 2Captcha's own auto-solve (armed
                    # above) already had its chance inside fetch_one()'s
                    # own navigation — a second, LOCAL solve attempt here
                    # would be redundant (captcha_solver.py's own module
                    # docstring: "over --cdp-endpoint, none of this
                    # applies").
                    if client is not None and not cdp_endpoint:
                        _maybe_solve_captcha(
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
                # error_kind == "not_found" is a completed (not failed)
                # ticker with zero rows contributed — unchanged.
        finally:
            browser.close()

    return ratings, completed, failed, blocked


def _run_via_scraper_api(
    tickers: List[str], *, client: TwoCaptchaClient, timeout: int, cdp_url: Optional[str],
    block_retries: int,
) -> Tuple[List[TickerRating], List[str], List[str], List[str]]:
    """--scraper-api's own top-level loop — no browser at all, so no
    `--headless`/`--executable-path`/context/page concepts apply. One
    Scraper API call per ticker, retried up to `--block-retries` times on
    a blocked outcome before giving up on that ticker (same "retry before
    you rotate" policy as the browser path, just without a browser
    session to keep — 2Captcha's own infrastructure decides its exit per
    call in this mode, see scraper_api_client.py)."""
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
             "rendered page — see _fetch_one_via_scraper_api()'s docstring for the untested "
             "assumption this rests on (whether payload.json answers a fresh, cookie-less request "
             "the same way it answers the in-page fetch this repo's browser engines use). "
             "--headed/--executable-path/--proxy/--cdp-endpoint/--fingerprint are all IGNORED in "
             "this mode (a single static fetch per ticker has no browser session, and brings its "
             "own exit IP/device) — set together, they log a warning rather than silently doing "
             "nothing.",
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
        proxies = []
        try:
            proxies = load_proxies(args.proxy, args.proxy_file)
        except ProxyParseError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_BAD_USAGE
        pool = ProxyPool(proxies, shuffle=args.proxy_shuffle) if proxies else None

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
