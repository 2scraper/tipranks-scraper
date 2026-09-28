#!/usr/bin/env python3
"""smoke_test.py — offline test suite. No engine driver required: it must
pass with NONE of playwright/selenium/pyppeteer installed, same as every
2scraper family member's offline suite (CLAUDE.md §6). Run with
`python3 smoke_test.py`.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "tests" / "fixtures"

_checks = []
_failures = []


def check(name):
    def decorator(fn):
        _checks.append((name, fn))
        return fn
    return decorator


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# tipranks_parser.py
# --------------------------------------------------------------------------- #
@check("find_ticker_record picks the requested ticker, not stocks[0]")
def _():
    from tipranks_parser import find_ticker_record

    payload = _load_fixture("tipranks_aapl_stock_analysis.json")
    record = find_ticker_record(payload, "aapl")  # lower-case on purpose
    assert record is not None, "AAPL record not found"
    assert record["_id"] == "AAPL"
    assert find_ticker_record(payload, "adtn")["_id"] == "ADTN"
    assert find_ticker_record(payload, "NOPE") is None


@check("parse_payload extracts confirmed AAPL fields correctly")
def _():
    from tipranks_parser import parse_payload

    payload = _load_fixture("tipranks_aapl_stock_analysis.json")
    rating = parse_payload(
        payload, "aapl", source_url="https://example/", scraped_at="2026-09-27T00:00:00Z"
    )
    assert rating is not None
    assert rating.ticker == "AAPL"
    assert rating.company_name == "Apple"
    assert rating.smart_score == 7
    assert rating.smart_score_updated == "2026-09-27T00:00:00.000Z"
    assert rating.consensus_rating_id == "moderateBuy"
    assert rating.consensus_rating_label == "Moderate Buy"
    assert rating.analyst_count == 30
    assert rating.buy_count == 15
    assert rating.hold_count == 11
    assert rating.sell_count == 4
    assert abs(rating.price_target_average - 334.8982608695652) < 1e-9
    assert rating.price_target_high == 400
    assert rating.price_target_low == 245
    assert rating.currency == "USD"
    assert rating.best_consensus_rating_id == "moderateBuy"
    assert rating.best_analyst_count == 23
    assert rating.current_price == 319.97  # the "open" quote, per tradeTime
    assert rating.current_price_source == "open"


@check("parse_payload degrades gracefully when consensus fields are absent")
def _():
    from tipranks_parser import parse_payload

    payload = _load_fixture("tipranks_luv_sparse_consensus.json")
    rating = parse_payload(
        payload, "LUV", source_url="https://example/", scraped_at="2026-09-27T00:00:00Z"
    )
    assert rating is not None
    assert rating.consensus_rating_id is None
    assert rating.consensus_rating_label is None
    assert rating.analyst_count is None
    assert rating.buy_count is None
    assert abs(rating.price_target_average - 51.07142857142857) < 1e-9
    assert rating.current_price is None  # no quotes block in this fixture at all
    assert rating.company_name == "Southwest Airlines"  # confirmed live, not a guess


@check("parse_payload degrades gracefully when analystRatings is absent entirely")
def _():
    from tipranks_parser import parse_payload

    payload = _load_fixture("tipranks_gme_no_ratings.json")
    rating = parse_payload(
        payload, "gme", source_url="https://example/", scraped_at="2026-09-27T00:00:00Z"
    )
    assert rating is not None
    assert rating.smart_score == 8
    assert rating.consensus_rating_id is None
    assert rating.price_target_average is None
    assert rating.current_price is None


@check("parse_payload returns None for a ticker not present in the payload")
def _():
    from tipranks_parser import parse_payload

    payload = _load_fixture("tipranks_aapl_stock_analysis.json")
    assert parse_payload(payload, "MSFT", source_url="x", scraped_at="x") is None


@check("humanize_consensus_id formats every confirmed live id correctly")
def _():
    from tipranks_parser import humanize_consensus_id

    assert humanize_consensus_id("moderateBuy") == "Moderate Buy"
    assert humanize_consensus_id("strongBuy") == "Strong Buy"
    assert humanize_consensus_id("neutral") == "Neutral"
    assert humanize_consensus_id("moderateSell") == "Moderate Sell"
    assert humanize_consensus_id(None) is None
    assert humanize_consensus_id("") is None


@check("is_allowed() re-derives robots.txt answers from the captured text, longest-match-wins")
def _():
    from tipranks_parser import is_allowed

    assert is_allowed("/stocks/aapl/stock-analysis") is True
    assert is_allowed("/stocks/aapl/stock-analysis/payload.json") is True
    assert is_allowed("/api/users/getUserSettings2") is False
    assert is_allowed("/stocks/aapl/options-chain/some-page") is False
    assert is_allowed("/stocks/aapl/options-chain/unusual-options-activity") is True
    assert is_allowed("/insiders/aapl") is True


@check("ticker_page_url / ticker_payload_url lower-case the ticker (site is case-insensitive)")
def _():
    from tipranks_parser import ticker_page_url, ticker_payload_url

    assert ticker_page_url("AAPL") == "https://www.tipranks.com/stocks/aapl/stock-analysis"
    assert ticker_payload_url("AAPL").endswith("/stocks/aapl/stock-analysis/payload.json")
    # A share-class ticker with a dot resolves live (confirmed: BRK.B) —
    # the URL builder must not mangle it.
    assert ticker_page_url("BRK.B") == "https://www.tipranks.com/stocks/brk.b/stock-analysis"


@check("ticker normalization strips harmless whitespace and rejects URL/path injection")
def _():
    from tipranks_parser import normalize_ticker, ticker_page_url

    assert normalize_ticker("  brk.b  ") == "BRK.B"
    assert normalize_ticker("BRK-B") == "BRK-B"
    for value in ("../../api/users", "AAPL?x=1", "/AAPL", "..", "AAPL/B"):
        try:
            ticker_page_url(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe ticker accepted: {value!r}")


@check("parse_payload tolerates malformed optional structures without crashing")
def _():
    from tipranks_parser import parse_payload

    assert parse_payload(
        {"models": {"stocks": [None]}}, "AAPL", source_url="x", scraped_at="x"
    ) is None
    rating = parse_payload(
        {"models": {"stocks": [{"_id": "AAPL", "analystRatings": "changed", "quotes": []}]}},
        "AAPL", source_url="x", scraped_at="x",
    )
    assert rating is not None
    assert rating.consensus_rating_id is None
    assert rating.current_price is None


# --------------------------------------------------------------------------- #
# output_writer.py
# --------------------------------------------------------------------------- #
@check("finish_run outcome precedence: remote_api_error > blocked > empty > partial > complete")
def _():
    import output_writer as ow

    def outcome(**kwargs):
        base = dict(
            ratings=[], out_path="/tmp/_smoke_out.json", fmt="json", engine="playwright",
            tickers_requested=["AAPL"], tickers_completed=[], failed_tickers=[],
            blocked=False, remote_api_error=False, allow_empty=True, started_at=0.0,
        )
        base.update(kwargs)
        return ow.finish_run(**base)

    assert outcome(remote_api_error=True) == ow.EXIT_REMOTE_API_ERROR
    assert outcome(remote_api_error=True, blocked=True) == ow.EXIT_REMOTE_API_ERROR
    assert outcome(blocked=True) == ow.EXIT_BLOCKED
    assert outcome() == ow.EXIT_ZERO_PRODUCTS
    assert outcome(
        ratings=[_dummy_rating()], tickers_completed=["AAPL"], failed_tickers=["MSFT"]
    ) == ow.EXIT_PARTIAL
    assert outcome(ratings=[_dummy_rating()], tickers_completed=["AAPL"]) == ow.EXIT_OK


@check("finish_run never writes output or a sidecar for a zero-result run without --allow-empty")
def _():
    import output_writer as ow

    out_path = "/tmp/_smoke_zero.json"
    for p in (out_path, ow.meta_path_for(out_path)):
        Path(p).unlink(missing_ok=True)

    exit_code = ow.finish_run(
        ratings=[], out_path=out_path, fmt="json", engine="playwright",
        tickers_requested=["ZZZ"], tickers_completed=["ZZZ"], failed_tickers=[],
        blocked=False, remote_api_error=False, allow_empty=False, started_at=0.0,
    )
    assert exit_code == ow.EXIT_ZERO_PRODUCTS
    assert not Path(out_path).exists()
    assert not Path(ow.meta_path_for(out_path)).exists()


@check("diff_runs.py correctly reports added/removed/rating/price-target/smart-score changes")
def _():
    import output_writer as ow
    import diff_runs
    from tipranks_parser import TickerRating

    old_path, new_path = "/tmp/_smoke_diff_old.json", "/tmp/_smoke_diff_new.json"

    old_aapl = TickerRating(
        ticker="AAPL", source="tipranks", source_url="x", scraped_at="t",
        consensus_rating_id="moderateBuy", consensus_rating_label="Moderate Buy",
        price_target_average=330.0, smart_score=7,
    )
    old_msft = TickerRating(
        ticker="MSFT", source="tipranks", source_url="x", scraped_at="t",
        consensus_rating_id="strongBuy", price_target_average=570.0, smart_score=9,
    )
    new_aapl = TickerRating(
        ticker="AAPL", source="tipranks", source_url="x", scraped_at="t",
        consensus_rating_id="strongBuy", consensus_rating_label="Strong Buy",
        price_target_average=345.0, smart_score=8,
    )
    new_nvda = TickerRating(
        ticker="NVDA", source="tipranks", source_url="x", scraped_at="t",
        consensus_rating_id="strongBuy", price_target_average=200.0, smart_score=10,
    )

    ow.finish_run(
        ratings=[old_aapl, old_msft], out_path=old_path, fmt="json", engine="playwright",
        tickers_requested=["AAPL", "MSFT"], tickers_completed=["AAPL", "MSFT"], failed_tickers=[],
        blocked=False, remote_api_error=False, allow_empty=False, started_at=0.0,
    )
    ow.finish_run(
        ratings=[new_aapl, new_nvda], out_path=new_path, fmt="json", engine="playwright",
        tickers_requested=["AAPL", "NVDA"], tickers_completed=["AAPL", "NVDA"], failed_tickers=[],
        blocked=False, remote_api_error=False, allow_empty=False, started_at=0.0,
    )

    result = diff_runs.diff(old_path, new_path)
    assert result["added"] == ["NVDA"]
    assert result["removed"] == ["MSFT"]
    assert [r["ticker"] for r in result["rating_changed"]] == ["AAPL"]
    assert [r["ticker"] for r in result["price_target_changed"]] == ["AAPL"]
    assert [r["ticker"] for r in result["smart_score_changed"]] == ["AAPL"]


@check("diff_runs rejects an output paired with a stale checksummed sidecar")
def _():
    import output_writer as ow
    import diff_runs

    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "run.json")
        ow.finish_run(
            ratings=[_dummy_rating()], out_path=path, fmt="json", engine="playwright",
            tickers_requested=["AAPL"], tickers_completed=["AAPL"], failed_tickers=[],
            blocked=False, remote_api_error=False, allow_empty=False, started_at=0.0,
        )
        meta = json.loads(Path(path + ".meta.json").read_text())
        assert len(meta["output_sha256"]) == 64
        Path(path).write_text("[]", encoding="utf-8")
        try:
            diff_runs.diff(path, path)
        except SystemExit as exc:
            assert "checksum mismatch" in str(exc)
        else:
            raise AssertionError("stale sidecar was accepted")


@check("write_json / write_csv round-trip the full TickerRating schema")
def _():
    import csv as csv_mod

    import output_writer as ow

    rating = _dummy_rating()
    json_path = "/tmp/_smoke_rt.json"
    csv_path = "/tmp/_smoke_rt.csv"
    ow.write_json([rating], json_path)
    ow.write_csv([rating], csv_path)

    written = json.loads(Path(json_path).read_text(encoding="utf-8"))
    assert written[0]["ticker"] == "AAPL"

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv_mod.DictReader(f))
    assert rows[0]["ticker"] == "AAPL"
    assert set(rows[0].keys()) == set(ow.RATING_FIELD_NAMES)


def _dummy_rating():
    from tipranks_parser import TickerRating

    return TickerRating(
        ticker="AAPL", source="tipranks", source_url="https://example/",
        scraped_at="2026-09-27T00:00:00Z", company_name="Apple",
    )


# --------------------------------------------------------------------------- #
# All three engines — importable with NO engine driver installed at all
# (CLAUDE.md §6), and their non-network paths runnable without one.
# --------------------------------------------------------------------------- #
import playwright_scraper
import selenium_scraper
import puppeteer_scraper

ENGINE_MODULES = [playwright_scraper, selenium_scraper, puppeteer_scraper]


@check("every engine imports cleanly with no engine driver installed")
def _():
    for mod in ENGINE_MODULES:
        assert hasattr(mod, "main"), f"{mod.__name__} has no main()"
        assert hasattr(mod, "is_allowed"), f"{mod.__name__} did not import tipranks_parser.is_allowed"


@check("every engine rejects bad usage identically, without touching the network")
def _():
    import output_writer as ow

    for mod in ENGINE_MODULES:
        assert mod.main([]) == ow.EXIT_BAD_USAGE, f"{mod.__name__}: no tickers should be bad usage"
        # A malformed --tickers-file path is also bad usage, caught before
        # any ticker reaches run() (and therefore before any browser
        # launches).
        assert mod.main(["--tickers-file", "/no/such/file.txt"]) == ow.EXIT_BAD_USAGE, mod.__name__


@check("every engine refuses a ticker path robots.txt disallows, before any network call")
def _():
    import output_writer as ow

    # There is no real disallowed ticker path (robots.txt disallows /api/*,
    # not /stocks/*), so this exercises the guard itself directly instead
    # of relying on a real disallowed ticker existing. Patch the name as
    # bound inside each engine's own namespace (a `from x import y` binds a
    # separate reference there, not an attribute on tipranks_parser).
    for mod in ENGINE_MODULES:
        original = mod.is_allowed
        mod.is_allowed = lambda path: False
        try:
            assert mod.main(["--ticker", "AAPL"]) == ow.EXIT_BAD_USAGE, mod.__name__
        finally:
            mod.is_allowed = original


@check("every engine accepts --executable-path (a real fix for a confirmed-live "
       "puppeteer bug, kept for CLI parity across all three per CLAUDE.md §4)")
def _():
    for mod in ENGINE_MODULES:
        args = mod.parse_args(["--ticker", "AAPL", "--executable-path", "/some/chrome"])
        assert args.executable_path == "/some/chrome", mod.__name__
        # Default stays None — this is an opt-in escape hatch, not a
        # required flag; every earlier check in this file calls main()
        # without it and must keep working unchanged.
        assert mod.parse_args(["--ticker", "AAPL"]).executable_path is None, mod.__name__


@check("a total run failure (every ticker fails, none complete) is EXIT_REMOTE_API_ERROR, "
       "not EXIT_ZERO_PRODUCTS — a regression found live: all three engines were passing "
       "remote_api_error=False unconditionally, so a network outage looked identical to "
       "the site legitimately returning nothing")
def _():
    import output_writer as ow

    def fake_run_total_failure(tickers, **kwargs):
        return [], [], list(tickers), []  # nothing completed, nothing blocked either

    for mod in ENGINE_MODULES:
        original = mod.run
        mod.run = fake_run_total_failure
        try:
            code = mod.main(["--ticker", "AAPL"])
            assert code == ow.EXIT_REMOTE_API_ERROR, f"{mod.__name__}: got {code}"
        finally:
            mod.run = original


@check("every engine can actually reach EXIT_BLOCKED via a real 403/429, "
       "even when every ticker in the run was blocked (not just some)")
def _():
    import output_writer as ow

    def fake_run_all_blocked(tickers, **kwargs):
        return [], [], list(tickers), list(tickers)  # all failed, all of them "blocked"

    for mod in ENGINE_MODULES:
        original = mod.run
        mod.run = fake_run_all_blocked
        try:
            code = mod.main(["--ticker", "AAPL"])
            assert code == ow.EXIT_BLOCKED, f"{mod.__name__}: got {code}"
        finally:
            mod.run = original


@check("main() never short-circuits on 'driver not installed' BEFORE calling a monkeypatched "
       "run() — regression test for a real gap found live on Roman's own machine 2026-09-28: "
       "selenium_scraper.py/puppeteer_scraper.py briefly had a `webdriver is None`/"
       "`pyppeteer_launch is None` guard placed directly in main(), which fired unconditionally, "
       "before run() (or the fake replacing it) was ever called — the driver-not-installed check "
       "belongs ONLY inside run() itself, same as playwright_scraper.py, so a caller that replaces "
       "run() (this smoke test's own fake_run_* checks, or a real embedder) still gets called. This "
       "was invisible in a sandbox that happened to have every engine driver installed — every "
       "fake_run_* check above passed there while being silently short-circuited on a machine "
       "without the drivers, which is exactly this suite's whole point (CLAUDE.md §6: 'no engine "
       "driver installed at all'). Forces the driver symbol to None here, regardless of what is "
       "actually installed in whatever environment runs this suite, so this can never again pass "
       "for the wrong reason.")
def _():
    import output_writer as ow

    driver_attr = {
        playwright_scraper: "sync_playwright",
        selenium_scraper: "webdriver",
        puppeteer_scraper: "pyppeteer_launch",
    }

    def fake_run_all_blocked(tickers, **kwargs):
        return [], [], list(tickers), list(tickers)

    for mod in ENGINE_MODULES:
        attr = driver_attr[mod]
        original_driver = getattr(mod, attr)
        original_run = mod.run
        setattr(mod, attr, None)
        mod.run = fake_run_all_blocked
        try:
            code = mod.main(["--ticker", "AAPL"])
            assert code == ow.EXIT_BLOCKED, (
                f"{mod.__name__}: got {code} with {attr}=None — main() short-circuited "
                f"before calling the monkeypatched run(), instead of EXIT_BLOCKED (3)"
            )
        finally:
            setattr(mod, attr, original_driver)
            mod.run = original_run


@check("a genuine partial run (one ticker succeeds, another fails with a plain "
       "remote error) is still EXIT_PARTIAL, not swallowed by the total-failure fix above")
def _():
    import output_writer as ow
    from tipranks_parser import TickerRating

    def fake_run_partial(tickers, **kwargs):
        good, bad = tickers[0], (tickers[1] if len(tickers) > 1 else "ZZZ")
        rating = TickerRating(ticker=good, source="tipranks", source_url="x", scraped_at="x")
        return [rating], [good], [bad], []

    for mod in ENGINE_MODULES:
        original = mod.run
        mod.run = fake_run_partial
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = str(Path(tmp) / "partial.json")
                code = mod.main([
                    "--ticker", "AAPL", "--ticker", "MSFT",
                    "--allow-empty", "--out", out,
                ])
                assert code == ow.EXIT_PARTIAL, f"{mod.__name__}: got {code}"
        finally:
            mod.run = original


@check("a total mixed block + network failure follows remote_api_error precedence")
def _():
    import output_writer as ow

    def fake_run_mixed(tickers, **kwargs):
        return [], [], list(tickers), [tickers[0]]

    for mod in ENGINE_MODULES:
        original = mod.run
        mod.run = fake_run_mixed
        try:
            code = mod.main(["--ticker", "AAPL", "--ticker", "MSFT"])
            assert code == ow.EXIT_REMOTE_API_ERROR, f"{mod.__name__}: got {code}"
        finally:
            mod.run = original


@check("playwright navigation errors degrade to a per-ticker remote error")
def _():
    class BrokenPage:
        def goto(self, *args, **kwargs):
            raise RuntimeError("ERR_NAME_NOT_RESOLVED")

    rating, error = playwright_scraper.fetch_one(BrokenPage(), "AAPL")
    assert rating is None
    assert error == "remote_api_error"


# --------------------------------------------------------------------------- #
# 2Captcha toolkit (env_config, proxy_pool, captcha_solver,
# scraper_api_client) — added 2026-09-28, ported from shein-scraper/
# g2-scraper after a real Cloudflare block was observed (CHANGELOG.md
# 0.2.0). See playwright_scraper.py's own module docstring for why: a
# real block, not a confirmed CAPTCHA widget.
# --------------------------------------------------------------------------- #
import env_config
import proxy_pool as _proxy_pool
import scraper_api_client


@check("env_config.ENV_KEYS matches .env.example exactly, in both directions")
def _():
    # Guarded like the credential-scanner/sample_output/pyproject checks
    # below: the Docker image's own COPY list deliberately never includes
    # .env.example (contributor-facing documentation, not something the
    # shipped image needs — see Dockerfile), so this must skip rather
    # than crash there. Confirmed live: this exact unguarded-read shape
    # broke the Docker build for the pyproject/changelog check in 0.2.1
    # before that one got the same fix.
    example_path = ROOT / ".env.example"
    if not example_path.exists():
        return
    example = example_path.read_text(encoding="utf-8")
    documented = {line.split("=", 1)[0] for line in example.splitlines() if "=" in line and not line.startswith("#")}
    assert documented == set(env_config.ENV_KEYS), (documented, set(env_config.ENV_KEYS))


@check("env_config uses TWOCAPTCHA_KEY/TIPRANKS_ prefixed keys only, not a leftover other-site name")
def _():
    for key in env_config.ENV_KEYS:
        assert key == "TWOCAPTCHA_KEY" or key.startswith("TIPRANKS_"), f"unexpected env key {key!r}"


@check("env_config._is_placeholder treats a braced {...} fragment as unset")
def _():
    assert env_config._is_placeholder("")
    assert env_config._is_placeholder(None)
    assert env_config._is_placeholder(
        "ws://{login}-zone-scraping_browser-country-{cc}-pid-{profileId}:{password}@cb.2captcha.com:9222"
    )
    assert not env_config._is_placeholder("a-real-looking-value-123")


@check("env_config.apply_env never overrides an explicitly-set CLI flag")
def _():
    import argparse
    import os as _os

    ns = argparse.Namespace(proxy="http://explicit:pass@host:1")
    _os.environ["TIPRANKS_PROXY"] = "http://from-env:pass@host:2"
    try:
        env_config.apply_env(ns, dotenv_path="/nonexistent/.env")
        assert ns.proxy == "http://explicit:pass@host:1"
    finally:
        del _os.environ["TIPRANKS_PROXY"]


@check("proxy_pool rejects a malformed proxy string with ProxyParseError")
def _():
    try:
        _proxy_pool.parse_proxy_line("not a proxy")
    except _proxy_pool.ProxyParseError:
        pass
    else:
        raise AssertionError("malformed proxy line was accepted")


@check("proxy_pool parses a credentialed proxy and masks the password (not the login) in logs")
def _():
    proxy = _proxy_pool.parse_proxy_line("http://user1234:pass5678@1.2.3.4:8080")
    assert proxy.host == "1.2.3.4"
    assert proxy.port == 8080
    assert proxy.has_auth
    masked = proxy.masked()
    assert "pass5678" not in masked
    assert "user1234" in masked


@check("proxy_pool.redact_credentials strips login:password out of an arbitrary string (an exception message is a log — CLAUDE.md §8)")
def _():
    text = "connect_over_cdp failed: ws://realuser:realpass1234@cb.2captcha.com:9222 timed out"
    redacted = _proxy_pool.redact_credentials(text)
    assert "realuser" not in redacted
    assert "realpass1234" not in redacted


@check("scraper_api_client.TwoCaptchaClient._require_key rejects a missing/empty key")
def _():
    client = scraper_api_client.TwoCaptchaClient(None)
    try:
        client._require_key()
    except scraper_api_client.TwoCaptchaAuthError:
        pass
    else:
        raise AssertionError("missing key was accepted")


@check("scraper_api_client honors --captcha-api/--scraper-api-url overrides, not the module-level defaults")
def _():
    client = scraper_api_client.TwoCaptchaClient(
        "fake-key", api_base="https://mock.example/api", scraper_api_base="https://mock.example/scraper",
    )
    assert client.api_base == "https://mock.example/api"
    assert client.scraper_api_base == "https://mock.example/scraper"


@check("all three engines define --scraper-api-cdp/--scraper-api-country/--scraper-api-profile-id, and pass cdp_url through to scraper_api_client.scrape_url (2026-09-28 port from shein-scraper/g2-scraper)")
def _():
    for path in ("playwright_scraper.py", "selenium_scraper.py", "puppeteer_scraper.py"):
        src = (ROOT / path).read_text(encoding="utf-8")
        assert '"--scraper-api-cdp"' in src, f"{path}: no --scraper-api-cdp flag"
        assert '"--scraper-api-country"' in src, f"{path}: no --scraper-api-country flag"
        assert '"--scraper-api-profile-id"' in src, f"{path}: no --scraper-api-profile-id flag"
        assert "cdp_url=cdp_url" in src, f"{path}: the scraper-api fetch path is not called with cdp_url"
        assert "scraping_browser_connection_url(" in src, f"{path}: --scraper-api-cdp never builds a Scraping Browser URL"
        assert "--scraper-api-cdp requires --scraper-api" in src, f"{path}: --scraper-api-cdp isn't guarded to require --scraper-api"


@check("BEHAVIORAL proof, all three engines: --scraper-api-cdp actually builds a country/profile-pinned Scraping Browser URL and it reaches scraper_api_client.scrape_url's cdp_url argument; without the flag cdp_url stays None; --scraper-api-cdp without --scraper-api is EXIT_BAD_USAGE, not a silent no-op")
def _():
    import output_writer as ow

    for mod in ENGINE_MODULES:
        captured = {}
        original = scraper_api_client.TwoCaptchaClient.scrape_url

        def _fake_scrape_url(self, url, *, data_format="raw", timeout=60, wait_for=None, cdp_url=None):
            captured["cdp_url"] = cdp_url
            return scraper_api_client.ScrapeResult(target_status=200, headers={}, body='{"models": {"stocks": []}}')

        scraper_api_client.TwoCaptchaClient.scrape_url = _fake_scrape_url
        try:
            # --scraper-api-cdp without --scraper-api: EXIT_BAD_USAGE, before
            # any network-shaped call is made.
            rc = mod.main(["--ticker", "AAPL", "--scraper-api-cdp"])
            assert rc == ow.EXIT_BAD_USAGE, f"{mod.__name__}: got {rc}"

            with tempfile.TemporaryDirectory() as td:
                mod.main([
                    "--ticker", "AAPL",
                    "--twocaptcha-key", "fake-key-for-test-only",
                    "--scraper-api", "--scraper-api-cdp",
                    "--scraper-api-country", "us", "--scraper-api-profile-id", "smoke-test-profile",
                    "--out", str(Path(td) / "out.json"), "--allow-empty",
                ])
            cdp_url = captured.get("cdp_url")
            assert cdp_url, f"{mod.__name__}: --scraper-api-cdp did not produce a cdp_url"
            assert "-country-us" in cdp_url, f"{mod.__name__}: cdp_url missing requested country: {cdp_url!r}"
            assert "-pid-smoke-test-profile" in cdp_url, f"{mod.__name__}: cdp_url missing requested profile id: {cdp_url!r}"

            captured.clear()
            with tempfile.TemporaryDirectory() as td:
                mod.main([
                    "--ticker", "AAPL",
                    "--twocaptcha-key", "fake-key-for-test-only",
                    "--scraper-api",
                    "--out", str(Path(td) / "out.json"), "--allow-empty",
                ])
            assert captured.get("cdp_url") is None, f"{mod.__name__}: cdp_url should be None without --scraper-api-cdp, got {captured.get('cdp_url')!r}"
        finally:
            scraper_api_client.TwoCaptchaClient.scrape_url = original


@check("--scraper-api fetches ticker_payload_url() directly (the JSON endpoint), not the rendered page — this repo's own deliberate divergence from shein-scraper/g2-scraper, see _fetch_one_via_scraper_api's docstring")
def _():
    original = scraper_api_client.TwoCaptchaClient.scrape_url
    captured = {}

    def _fake_scrape_url(self, url, *, data_format="raw", timeout=60, wait_for=None, cdp_url=None):
        captured["url"] = url
        return scraper_api_client.ScrapeResult(target_status=200, headers={}, body='{"models": {"stocks": []}}')

    scraper_api_client.TwoCaptchaClient.scrape_url = _fake_scrape_url
    try:
        for mod in ENGINE_MODULES:
            captured.clear()
            with tempfile.TemporaryDirectory() as td:
                mod.main([
                    "--ticker", "AAPL", "--twocaptcha-key", "fake-key-for-test-only", "--scraper-api",
                    "--out", str(Path(td) / "out.json"), "--allow-empty",
                ])
            assert captured.get("url", "").endswith("/stocks/aapl/stock-analysis/payload.json"), \
                f"{mod.__name__}: {captured.get('url')!r}"
    finally:
        scraper_api_client.TwoCaptchaClient.scrape_url = original


@check("every engine's --scraper-api mode actually parses a real payload and reports EXIT_OK when the fake fetch returns real fixture data")
def _():
    import output_writer as ow

    original = scraper_api_client.TwoCaptchaClient.scrape_url
    fixture_body = (FIXTURES / "tipranks_aapl_stock_analysis.json").read_text(encoding="utf-8")

    def _fake_scrape_url(self, url, *, data_format="raw", timeout=60, wait_for=None, cdp_url=None):
        return scraper_api_client.ScrapeResult(target_status=200, headers={}, body=fixture_body)

    scraper_api_client.TwoCaptchaClient.scrape_url = _fake_scrape_url
    try:
        for mod in ENGINE_MODULES:
            with tempfile.TemporaryDirectory() as td:
                rc = mod.main([
                    "--ticker", "AAPL", "--twocaptcha-key", "fake-key-for-test-only", "--scraper-api",
                    "--out", str(Path(td) / "out.json"),
                ])
            assert rc == ow.EXIT_OK, f"{mod.__name__}: got {rc}"
    finally:
        scraper_api_client.TwoCaptchaClient.scrape_url = original


@check("every engine's --scraper-api mode treats a Cloudflare block marker in the fetched body as blocked, not a clean empty result")
def _():
    import output_writer as ow

    original = scraper_api_client.TwoCaptchaClient.scrape_url

    def _fake_scrape_url(self, url, *, data_format="raw", timeout=60, wait_for=None, cdp_url=None):
        return scraper_api_client.ScrapeResult(target_status=403, headers={}, body="<html><body>Just a moment...</body></html>")

    scraper_api_client.TwoCaptchaClient.scrape_url = _fake_scrape_url
    try:
        for mod in ENGINE_MODULES:
            rc = mod.main(["--ticker", "AAPL", "--twocaptcha-key", "fake-key-for-test-only", "--scraper-api"])
            assert rc == ow.EXIT_BLOCKED, f"{mod.__name__}: got {rc}"
    finally:
        scraper_api_client.TwoCaptchaClient.scrape_url = original


@check("every engine's _maybe_solve_captcha is a documented no-op when no client is configured or policy is 'off' — never raises, never touches the network")
def _():
    import asyncio as _asyncio

    for mod in ENGINE_MODULES:
        fn = mod._maybe_solve_captcha
        kwargs = dict(client=None, ticker="AAPL", policy="when-blocked", min_score=0.3)
        if mod is playwright_scraper:
            fn(page=None, **kwargs)
        elif mod is selenium_scraper:
            fn(driver=None, **kwargs)
        elif mod is puppeteer_scraper:
            _asyncio.get_event_loop().run_until_complete(fn(page=None, **kwargs))
        else:
            raise AssertionError(f"unrecognised engine module {mod.__name__}")


@check("every engine accepts --block-retries (default 2, matching the rest of the family)")
def _():
    for mod in ENGINE_MODULES:
        args = mod.parse_args(["--ticker", "AAPL"])
        assert args.block_retries == 2, mod.__name__
        args = mod.parse_args(["--ticker", "AAPL", "--block-retries", "5"])
        assert args.block_retries == 5, mod.__name__


@check("selenium refuses a credentialed --cdp-endpoint outright (EXIT_BAD_USAGE) — chromedriver cannot authenticate a remote CDP session, unlike Playwright/Puppeteer (CLAUDE.md §6)")
def _():
    import output_writer as ow

    rc = selenium_scraper.main([
        "--ticker", "AAPL", "--cdp-endpoint", "ws://login:pass@cb.2captcha.com:9222",
    ])
    assert rc == ow.EXIT_BAD_USAGE, f"got {rc}"


@check("playwright_scraper._looks_blocked / puppeteer_scraper._looks_blocked catch the confirmed-real Cloudflare markers (URL token or body text), independent of HTTP status")
def _():
    for mod in (playwright_scraper, puppeteer_scraper):
        assert mod._looks_blocked(None, "https://x/?__cf_chl_rt_tk=abc", "") is True, mod.__name__
        assert mod._looks_blocked(None, "https://x/", "Just a moment...") is True, mod.__name__
        assert mod._looks_blocked(200, "https://x/", "") is False, mod.__name__
        assert mod._looks_blocked(403, "https://x/", "") is True, mod.__name__


# --------------------------------------------------------------------------- #
# repository hygiene
# --------------------------------------------------------------------------- #
@check("no banned wording anywhere in the shipped repo")
def _():
    banned = [
        "cloud browser",
        "antidetect browser",
        "2scraper antidetect browser",
        "gate.2prx.com",
        "--antidetect",
        "antidetect_local_api",
    ]
    exts = (".py", ".md", ".html", ".toml", ".cfg", ".yml", ".yaml")
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in exts:
            continue
        if path.name in ("smoke_test.py",) or path.name.startswith("2scraper"):
            continue
        if "/.git/" in str(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for phrase in banned:
            assert phrase not in text, f"banned phrase {phrase!r} found in {path}"


@check("the single credential scanner passes; skip gracefully if it isn't in this checkout")
def _():
    # Guarded like the checks below it: a Docker build's COPY list is a
    # deliberately stripped-down context that never includes .github/, so
    # this must skip rather than FileNotFoundError there (same reasoning
    # as the two checks below).
    scanner = ROOT / ".github" / "ci_checks.py"
    if not scanner.exists():
        return
    import subprocess

    result = subprocess.run(
        [sys.executable, str(scanner)], cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"ci_checks.py failed:\n{result.stdout}\n{result.stderr}"


@check("credential scanner recognizes quoted and unquoted secret assignments, and does not flag a Python type-hint token as a secret")
def _():
    import importlib.util

    scanner = ROOT / ".github" / "ci_checks.py"
    if not scanner.exists():
        return
    spec = importlib.util.spec_from_file_location("tipranks_ci_checks", scanner)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    oauth_name = "CLAUDE_CODE" + "_OAUTH_TOKEN"
    api_name = "api" + "_key"
    value = "realvalue" + "123456"
    for line in (
        f'{oauth_name}="{value}"',
        f"{oauth_name}={value}",
        f"{api_name}: {value}",
    ):
        assert module.SECRET_ASSIGNMENT.search(line), line
    # Regression: `api_key: Optional[str]` in a function signature matches
    # the same NAME(:|=)VALUE shape a real unquoted secret assignment
    # would — this is exactly what scraper_api_client.py's __init__ looks
    # like, and it must NOT be reported (see .github/ci_checks.py's own
    # docstring for the incident this fixed: 29/30 before this guard).
    hint_line = f"{api_name}: Optional[str]"
    match = module.SECRET_ASSIGNMENT.search(hint_line)
    assert match is not None  # the regex itself still matches the shape
    assert module._looks_like_type_hint(match.group(3))


@check("sample_output.{json,csv} match the TickerRating schema, if they exist yet")
def _():
    import csv as csv_mod

    import output_writer as ow

    json_path = ROOT / "sample_output.json"
    csv_path = ROOT / "sample_output.csv"
    if not json_path.exists() or not csv_path.exists():
        return  # part of the doc set, not yet required at every stage
    rows = json.loads(json_path.read_text(encoding="utf-8"))
    assert rows and set(rows[0]) == set(ow.RATING_FIELD_NAMES)
    with open(csv_path, newline="", encoding="utf-8") as f:
        header = next(csv_mod.reader(f))
    assert header == ow.RATING_FIELD_NAMES


@check("pyproject version has a matching released changelog section, if both files exist yet")
def _():
    # Neither file is part of the Docker image's COPY list (metadata for
    # contributors, not something the shipped image needs) -- skip
    # cleanly there instead of a bare FileNotFoundError, same as the
    # credential-scanner check above for .github/ci_checks.py.
    pyproject_path = ROOT / "pyproject.toml"
    changelog_path = ROOT / "CHANGELOG.md"
    if not pyproject_path.exists() or not changelog_path.exists():
        return
    pyproject = pyproject_path.read_text(encoding="utf-8")
    changelog = changelog_path.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    assert match, "pyproject.toml has no project version"
    assert f"## [{match.group(1)}]" in changelog


@check("Dockerfile, if present, removes fixtures/test suite and doesn't COPY a .env")
def _():
    dockerfile = ROOT / "Dockerfile"
    if not dockerfile.exists():
        return
    text = dockerfile.read_text(encoding="utf-8")
    assert "smoke_test.py" in text, "Dockerfile should run smoke_test.py at build time"
    assert re.search(r"rm\s+-rf\s+[^\n]*smoke_test", text), "Dockerfile must strip the test suite from the final layer"
    assert "COPY .env" not in text, "Dockerfile must never COPY a .env into the image"


def main() -> int:
    passed = 0
    for name, fn in _checks:
        try:
            fn()
            passed += 1
        except Exception as exc:  # noqa: BLE001
            _failures.append((name, exc))

    total = len(_checks)
    print(f"{passed}/{total} checks passed")
    for name, exc in _failures:
        print(f"FAIL: {name}\n      {exc}")
    return 0 if not _failures else 1


if __name__ == "__main__":
    sys.exit(main())
