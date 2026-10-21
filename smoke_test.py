#!/usr/bin/env python3
"""smoke_test.py — offline test suite. No engine driver required: it must
pass with NONE of playwright/selenium/pyppeteer installed, same as every
2scraper family member's offline suite. Run with
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

# A developer's own `.env` (or exported credentials) must not change what
# these checks see: with a credentialed TIPRANKS_CDP_ENDPOINT in `.env`,
# Selenium refused every run as bad usage and five checks failed. An empty
# exported variable outranks `.env` (env_config.apply_env), so blank them.
import os  # noqa: E402
import env_config  # noqa: E402

for _key in env_config.ENV_KEYS:
    os.environ[_key] = ""

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


@check("find_ticker_record picks the ticker's full record over its own sidebar stub listed first "
       "(live 2026-10-20: 55 of the S&P 500, e.g. ABNB, lost Smart Score and consensus)")
def _():
    from tipranks_parser import find_ticker_record, parse_payload

    payload = _load_fixture("tipranks_abnb_sidebar_stub_first.json")
    ids = [s["_id"] for s in payload["models"]["stocks"]]
    assert ids.count("ABNB") == 2 and "smartScore" not in payload["models"]["stocks"][ids.index("ABNB")], (
        "fixture must keep the stub before the full record"
    )
    assert "smartScore" in find_ticker_record(payload, "ABNB")
    row = parse_payload(payload, "ABNB", source_url="x", scraped_at="x")
    assert row.smart_score == 10, row.smart_score
    assert row.consensus_rating_id == "moderateBuy", row.consensus_rating_id
    assert (row.analyst_count, row.buy_count, row.hold_count, row.sell_count) == (33, 22, 10, 1)
    assert (row.price_target_high, row.price_target_low) == (220, 150)
    assert row.current_price is not None


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
    assert rating.current_price == 319.97  # the "open" quote: the fixture carries no fresher price
    assert rating.current_price_source == "open"


@check("current_price is the freshest of quotes / prices.daily / stocks_extra daily history (live 2026-10-19: quotes were months stale)")
def _():
    from tipranks_parser import parse_payload

    def rating(record, extra=None):
        payload = {"models": {"stocks": [dict({"_id": "NVDA"}, **record)], "stocks_extra": extra or []}}
        return parse_payload(payload, "NVDA", source_url="x", scraped_at="x")

    stale_quote = {"quotes": {"tradeTime": "open", "open": {"price": 209.33, "lastTrade": "2026-07-27T08:36:45.000Z"}}}
    daily = {"prices": {"daily": {"price": 214.13, "date": "2026-10-16T00:00:00.000Z"}}}
    history = [{"_id": "NVDA", "prices": {"daily": [
        {"date": "2026-10-15T00:00:00.000Z", "close": 217.23, "price": 217.23},
        {"date": "2026-10-16T00:00:00.000Z", "close": 214.13, "price": 214.13},
    ]}}]

    r = rating(dict(stale_quote, **daily))
    assert (r.current_price, r.current_price_source) == (214.13, "daily"), r
    assert r.current_price_asof == "2026-10-16T00:00:00.000Z"
    r = rating({}, history)  # LUV/BRK.B shape: no quotes, no prices on the record
    assert (r.current_price, r.current_price_source) == (214.13, "daily_history"), r
    fresh_quote = {"quotes": {"tradeTime": "open", "open": {"price": 215.0, "lastTrade": "2026-10-16T19:59:00.000Z"}}}
    r = rating(dict(fresh_quote, **daily), history)
    assert (r.current_price, r.current_price_source) == (215.0, "open"), r
    r = rating({})
    assert (r.current_price, r.current_price_asof, r.current_price_source) == (None, None, None)


@check("parse_payload reads dividends, earnings, insider / hedge fund / investor activity and "
       "news / blogger sentiment (live KO capture, 2026-10-20)")
def _():
    from tipranks_parser import parse_payload

    row = parse_payload(_load_fixture("tipranks_ko_extras.json"), "KO", source_url="x", scraped_at="x")
    assert (row.dividend_amount, row.dividend_frequency, row.dividend_annualized_payout) == (0.53, "quarterly", 2.1)
    assert row.dividend_ex_date == "2026-09-15T00:00:00.000Z" and row.dividend_pay_date == "2026-10-01T00:00:00.000Z"
    assert round(row.dividend_yield, 4) == 0.0253 and round(row.dividend_payout_ratio, 4) == 0.9204
    assert (row.next_dividend_amount, row.next_dividend_ex_date) == (0.53, "2026-12-01T00:00:00.000Z")
    assert (row.last_earnings_date, row.last_earnings_eps, row.last_earnings_eps_estimate) == (
        "2026-07-28T00:00:00.000Z", 0.97, 0.932)
    assert (row.next_earnings_date, row.next_earnings_time, row.next_earnings_eps_estimate) == (
        "2026-10-27T00:00:00.000Z", "PreMarket", 0.881)
    assert row.next_earnings_revenue_estimate == 12919928000
    assert (row.insider_sentiment, row.insider_signal, round(row.insider_net_usd)) == ("veryNegative", 0.0342, -33647685)
    assert (row.hedge_fund_sentiment, row.hedge_fund_signal, row.hedge_fund_net_shares) == ("positive", 0.6912, 3632264)
    assert (row.investor_sentiment, row.investor_signal) == ("positive", 0.4073)
    assert row.investor_change_7d > 0 and row.investor_change_30d > 0
    assert (row.news_sentiment, row.news_sentiment_score, row.news_bullish_ratio) == ("veryPositive", 1, 1)
    assert (row.news_articles_last_week, row.news_articles_weekly_average) == (55, 37)
    assert (row.blogger_sentiment, row.blogger_opinion_count) == ("positive", 70)
    assert round(row.blogger_bullish_ratio, 4) == 0.8143


@check("the extra columns stay None, not invented, for a non-payer with no insider activity "
       "(live TSLA capture) and for a record without any of those blocks (AAPL fixture)")
def _():
    from dataclasses import asdict

    from tipranks_parser import parse_payload

    row = parse_payload(_load_fixture("tipranks_tsla_no_dividend.json"), "TSLA", source_url="x", scraped_at="x")
    dividend_cols = [k for k in asdict(row) if "dividend" in k]
    assert len(dividend_cols) == 10 and all(getattr(row, k) is None for k in dividend_cols), dividend_cols
    assert row.insider_sentiment is None and row.insider_signal is None
    assert row.insider_net_usd == 0, "the site's own 0 for no insider activity"
    assert row.next_earnings_time == "AfterHours" and row.hedge_fund_sentiment == "veryNegative"

    row = asdict(parse_payload(_load_fixture("tipranks_aapl_stock_analysis.json"), "AAPL", source_url="x", scraped_at="x"))
    extras = list(row)[list(row).index("dividend_amount"):]
    assert len(extras) == 35 and all(row[k] is None for k in extras)


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


@check("the robots.txt capture is the 2026-10-20 one: its new query-string rules apply, and the two paths this repo fetches stay allowed")
def _():
    from tipranks_parser import ROBOTS_TXT_CAPTURE, is_allowed, parse_robots_rules

    rules = parse_robots_rules(ROBOTS_TXT_CAPTURE)
    # Added on the site between 2026-10-14 and 2026-10-20.
    for rule in (("disallow", "/*?hash="), ("disallow", "/*?llf="),
                 ("disallow", "/*?*s="), ("disallow", "/*?*id=")):
        assert rule in rules, rule
    # Dropped on the site in the same change.
    for rule in (("disallow", "*?*s="), ("disallow", "/news/earnings/"),
                 ("disallow", "/news/dividends/")):
        assert rule not in rules, rule
    assert is_allowed("/stocks/aapl/stock-analysis?hash=abc") is False
    assert is_allowed("/stocks/aapl/stock-analysis?llf=1") is False
    assert is_allowed("/news/earnings/some-article") is True
    # The only paths the engines request (no query string):
    assert is_allowed("/stocks/aapl/stock-analysis") is True
    assert is_allowed("/stocks/brk.b/stock-analysis/payload.json") is True


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
# All three engines — importable with NO engine driver installed at all,
# and their non-network paths runnable without one.
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
       "puppeteer bug, kept for CLI parity across all three engines)")
def _():
    for mod in ENGINE_MODULES:
        args = mod.parse_args(["--ticker", "AAPL", "--executable-path", "/some/chrome"])
        assert args.executable_path == "/some/chrome", mod.__name__
        # Default stays None — this is an opt-in escape hatch, not a
        # required flag; every earlier check in this file calls main()
        # without it and must keep working unchanged.
        assert mod.parse_args(["--ticker", "AAPL"]).executable_path is None, mod.__name__


@check("every engine's --tickers-file skips blank lines and # comments, like --proxy-file")
def _():
    import output_writer as ow

    seen = {}

    def fake_run(tickers, **kwargs):
        seen["tickers"] = list(tickers)
        return [], list(tickers), [], [], []

    for mod in ENGINE_MODULES:
        original = mod.run
        mod.run = fake_run
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "tickers.txt"
                path.write_text("# watchlist\n\nAAPL\n  # indented comment\nmsft  # Microsoft\nAAPL\n", encoding="utf-8")
                code = mod.main(["--tickers-file", str(path), "--out", str(Path(tmp) / "o.json")])
                assert seen["tickers"] == ["AAPL", "MSFT"], f"{mod.__name__}: {seen['tickers']}"
                assert code == ow.EXIT_ZERO_PRODUCTS, f"{mod.__name__}: got {code}"

                path.write_text("# only comments\n\n", encoding="utf-8")
                assert mod.main(["--tickers-file", str(path)]) == ow.EXIT_BAD_USAGE, mod.__name__
        finally:
            mod.run = original


@check("puppeteer finds an installed Chrome only on Apple Silicon macOS (its bundled Chromium "
       "segfaults there), and main() uses it unless --executable-path or --cdp-endpoint is given")
def _():
    from types import SimpleNamespace

    mod = puppeteer_scraper
    original_sys, original_platform, original_run = mod.sys, mod.platform, mod.run
    original_find = mod.installed_chrome
    seen = {}

    def fake_run(tickers, **kwargs):
        seen["executable_path"] = kwargs.get("executable_path")
        return [], list(tickers), [], [], []

    try:
        with tempfile.TemporaryDirectory() as tmp:
            chrome = Path(tmp) / "Google Chrome.app/Contents/MacOS/Google Chrome"
            chrome.parent.mkdir(parents=True)
            chrome.write_text("", encoding="utf-8")
            chrome.chmod(0o755)

            mod.sys = SimpleNamespace(platform="darwin")
            mod.platform = SimpleNamespace(machine=lambda: "arm64")
            assert mod.installed_chrome((tmp,)) == str(chrome)
            empty = str(Path(tmp) / "nothing-here")
            assert mod.installed_chrome((empty,)) is None
            mod.platform = SimpleNamespace(machine=lambda: "x86_64")
            assert mod.installed_chrome((tmp,)) is None
            mod.sys = SimpleNamespace(platform="linux")
            mod.platform = SimpleNamespace(machine=lambda: "arm64")
            assert mod.installed_chrome((tmp,)) is None
            mod.sys = original_sys

            mod.installed_chrome = lambda: "/found/chrome"
            mod.run = fake_run
            out = str(Path(tmp) / "o.json")
            mod.main(["--ticker", "AAPL", "--out", out])
            assert seen["executable_path"] == "/found/chrome", seen
            mod.main(["--ticker", "AAPL", "--out", out, "--executable-path", "/my/chrome"])
            assert seen["executable_path"] == "/my/chrome", seen
            mod.main(["--ticker", "AAPL", "--out", out, "--cdp-endpoint", "ws://127.0.0.1:9/x"])
            assert seen["executable_path"] is None, seen
    finally:
        mod.sys, mod.platform, mod.run = original_sys, original_platform, original_run
        mod.installed_chrome = original_find


@check("a total run failure (every ticker fails, none complete) is EXIT_REMOTE_API_ERROR, "
       "not EXIT_ZERO_PRODUCTS — a regression found live: all three engines were passing "
       "remote_api_error=False unconditionally, so a network outage looked identical to "
       "the site legitimately returning nothing")
def _():
    import output_writer as ow

    def fake_run_total_failure(tickers, **kwargs):
        return [], [], list(tickers), [], []  # nothing completed, nothing blocked either

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
        return [], [], list(tickers), list(tickers), []  # all failed, all of them "blocked"

    for mod in ENGINE_MODULES:
        original = mod.run
        mod.run = fake_run_all_blocked
        try:
            code = mod.main(["--ticker", "AAPL"])
            assert code == ow.EXIT_BLOCKED, f"{mod.__name__}: got {code}"
        finally:
            mod.run = original


@check("main() never short-circuits on 'driver not installed' BEFORE calling a monkeypatched "
       "run() — regression test for a real gap found live on a maintainer's machine 2026-09-28: "
       "selenium_scraper.py/puppeteer_scraper.py briefly had a `webdriver is None`/"
       "`pyppeteer_launch is None` guard placed directly in main(), which fired unconditionally, "
       "before run() (or the fake replacing it) was ever called — the driver-not-installed check "
       "belongs ONLY inside run() itself, same as playwright_scraper.py, so a caller that replaces "
       "run() (this smoke test's own fake_run_* checks, or a real embedder) still gets called. This "
       "was invisible in a sandbox that happened to have every engine driver installed — every "
       "fake_run_* check above passed there while being silently short-circuited on a machine "
       "without the drivers, which is exactly this suite's whole point (it must pass with no engine "
       "driver installed at all). Forces the driver symbol to None here, regardless of what is "
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
        return [], [], list(tickers), list(tickers), []

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
        return [rating], [good], [bad], [], []

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
        return [], [], list(tickers), [tickers[0]], []

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
# scraper_api_client) — added 2026-09-28 after a real Cloudflare block
# was observed (CHANGELOG.md 0.2.0). See playwright_scraper.py's own module docstring for why: a
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


@check("ProxyPool.replace_dead moves to the next live proxy in file order, wrapping, "
       "and leaves the last live proxy in place instead of failing the run")
def _():
    a, b, c = (_proxy_pool.parse_proxy_line(f"http://127.0.0.1:{port}") for port in (1, 2, 3))
    pool = _proxy_pool.ProxyPool([a, b, c])
    assert pool.next() is a
    assert pool.replace_dead(a) is b
    assert pool.replace_dead(b) is c
    assert pool.replace_dead(c) is None, "c is the last live proxy"
    assert pool.next() is c, "c must stay live"

    pool = _proxy_pool.ProxyPool([a, b, c])
    assert pool.replace_dead(c) is a, "wraps to the start of the list"
    assert _proxy_pool.ProxyPool([a]).replace_dead(a) is None
    assert pool.replace_dead(_proxy_pool.parse_proxy_line("http://127.0.0.1:9")) is None, "unknown proxy"


@check("every engine's fetch reports a dead proxy as 'proxy_dead' (so run() can switch "
       "to the next one) and any other navigation error as 'remote_api_error'")
def _():
    import asyncio

    def failing(message):
        def fail(*args, **kwargs):
            raise RuntimeError(message)
        return fail

    def afailing(message):
        async def fail(*args, **kwargs):
            raise RuntimeError(message)
        return fail

    dead = "net::ERR_PROXY_CONNECTION_FAILED at https://www.tipranks.com/x"
    other = "Timeout 30000ms exceeded.\nCall log: ..."
    for message, expected in ((dead, "proxy_dead"), (other, "remote_api_error")):
        page = type("Page", (), {"goto": staticmethod(failing(message))})()
        assert playwright_scraper.fetch_one(page, "AAPL") == (None, expected), (message, "playwright")

        driver = type("Driver", (), {
            "set_page_load_timeout": staticmethod(lambda *_: None),
            "get": staticmethod(failing("Message: unknown error: " + message)),
        })()
        assert selenium_scraper.fetch_one(driver, "AAPL") == (None, expected), (message, "selenium")

        apage = type("Page", (), {"goto": staticmethod(afailing(message))})()
        loop = asyncio.new_event_loop()
        try:
            got = loop.run_until_complete(puppeteer_scraper.fetch_one_async(apage, "AAPL"))
        finally:
            loop.close()
        assert got == (None, expected), (message, "puppeteer")


@check("proxy_pool parses a credentialed proxy and masks the password (not the login) in logs")
def _():
    proxy = _proxy_pool.parse_proxy_line("http://user1234:pass5678@1.2.3.4:8080")
    assert proxy.host == "1.2.3.4"
    assert proxy.port == 8080
    assert proxy.has_auth
    masked = proxy.masked()
    assert "pass5678" not in masked
    assert "user1234" in masked


@check("proxy_pool.redact_credentials strips login:password out of an arbitrary string (an exception message is a log too)")
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


@check("all three engines define --scraper-api-cdp/--scraper-api-country/--scraper-api-profile-id, and pass cdp_url through to scraper_api_client.scrape_url")
def _():
    for path in ("playwright_scraper.py", "selenium_scraper.py", "puppeteer_scraper.py"):
        src = (ROOT / path).read_text(encoding="utf-8")
        assert '"--scraper-api-cdp"' in src, f"{path}: no --scraper-api-cdp flag"
        assert '"--scraper-api-country"' in src, f"{path}: no --scraper-api-country flag"
        assert '"--scraper-api-profile-id"' in src, f"{path}: no --scraper-api-profile-id flag"
        assert "cdp_url=cdp_url" in src, f"{path}: the scraper-api fetch path is not called with cdp_url"
        assert "scraping_browser_connection_url(\n                    args.cdp_endpoint" in src, f"{path}: --scraper-api-cdp does not build its URL from --cdp-endpoint"
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

            # Without the user's own Scraping Browser endpoint there is no
            # login to build one from (the API key is not it): bad usage.
            rc = mod.main([
                "--ticker", "AAPL", "--twocaptcha-key", "fake-key-for-test-only",
                "--scraper-api", "--scraper-api-cdp",
            ])
            assert rc == ow.EXIT_BAD_USAGE, f"{mod.__name__}: --scraper-api-cdp without an endpoint got {rc}"
            assert "cdp_url" not in captured, f"{mod.__name__}: fetched without an endpoint"

            with tempfile.TemporaryDirectory() as td:
                mod.main([
                    "--ticker", "AAPL",
                    "--twocaptcha-key", "fake-key-for-test-only",
                    "--cdp-endpoint", "ws://sbuser-zone-scraping_browser-country-de:sbpass@cb.2captcha.com:9222",
                    "--scraper-api", "--scraper-api-cdp",
                    "--scraper-api-country", "us", "--scraper-api-profile-id", "smoke-test-profile",
                    "--out", str(Path(td) / "out.json"), "--allow-empty",
                ])
            cdp_url = captured.get("cdp_url")
            assert cdp_url == "ws://sbuser-zone-scraping_browser-country-us-pid-smoke-test-profile:sbpass@cb.2captcha.com:9222", (
                f"{mod.__name__}: cdp_url not built from the endpoint's own login/password with the requested country/profile: {cdp_url!r}"
            )
            assert "fake-key-for-test-only" not in cdp_url, f"{mod.__name__}: the API key leaked into the Scraping Browser login"

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


@check("--scraper-api fetches ticker_payload_url() directly (the JSON endpoint), not the rendered page — a deliberate choice for this repo, see _fetch_one_via_scraper_api's docstring")
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
        from captcha_solver import SolveBudget

        kwargs = dict(client=None, ticker="AAPL", policy="when-blocked", min_score=0.3, budget=SolveBudget())
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


@check("selenium refuses a credentialed --cdp-endpoint outright (EXIT_BAD_USAGE) — chromedriver cannot authenticate a remote CDP session, unlike Playwright/Puppeteer")
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


# --------------------------------------------------------------------------- #
# Output contract, paid-solve budget, flag validation, parser failure and
# credential-scan guards (2026-10-14 audit fixes).
# --------------------------------------------------------------------------- #
@check("atomic writer: a new file gets 0o666 & ~umask (022/002/077), an existing file keeps its mode, a failed write keeps the old file and leaves no temp file")
def _():
    import os as _os
    import output_writer as ow

    with tempfile.TemporaryDirectory() as td:
        for umask, expected in ((0o022, 0o644), (0o002, 0o664), (0o077, 0o600)):
            path = Path(td) / f"new-{umask:o}.json"
            old = _os.umask(umask)
            try:
                ow._atomic_write_text(str(path), "[]")
            finally:
                _os.umask(old)
            mode = path.stat().st_mode & 0o777
            assert mode == expected, f"umask {umask:o}: mode {mode:o}, expected {expected:o}"

        existing = Path(td) / "existing.json"
        existing.write_text("old", encoding="utf-8")
        _os.chmod(existing, 0o660)
        ow._atomic_write_text(str(existing), "new")
        assert existing.stat().st_mode & 0o777 == 0o660
        assert existing.read_text(encoding="utf-8") == "new"

        original_fsync = ow.os.fsync

        def _broken_fsync(fd):
            raise OSError("disk full")

        ow.os.fsync = _broken_fsync
        try:
            try:
                ow._atomic_write_text(str(existing), "should not land")
            except OSError:
                pass
            else:
                raise AssertionError("a failed fsync was swallowed")
        finally:
            ow.os.fsync = original_fsync
        assert existing.read_text(encoding="utf-8") == "new"
        leftovers = [p.name for p in Path(td).iterdir() if p.name.startswith(".existing.json.")]
        assert not leftovers, leftovers


@check("CSV output neutralises formula-shaped text cells, leaves numbers and JSON alone, and records csv_cells_escaped in the sidecar under the caller's extra_meta")
def _():
    import csv as csv_mod
    import output_writer as ow
    from tipranks_parser import TickerRating

    rating = TickerRating(
        ticker="AAPL", source="tipranks", source_url="https://example/",
        scraped_at="2026-10-14T00:00:00Z", company_name='=HYPERLINK("http://x","y")',
        consensus_rating_label="@SUM(A1)", price_target_upside=-3.25,
    )
    with tempfile.TemporaryDirectory() as td:
        out = str(Path(td) / "run.csv")
        code = ow.finish_run(
            ratings=[rating], out_path=out, fmt="csv", engine="playwright",
            tickers_requested=["AAPL"], tickers_completed=["AAPL"], failed_tickers=[],
            blocked=False, remote_api_error=False, allow_empty=False, started_at=0.0,
        )
        assert code == ow.EXIT_OK
        with open(out, newline="", encoding="utf-8") as f:
            row = next(csv_mod.DictReader(f))
        assert row["company_name"] == "'" + rating.company_name, row["company_name"]
        assert row["consensus_rating_label"] == "'@SUM(A1)"
        assert row["price_target_upside"] == "-3.25", "a negative number is data, not a formula"
        meta = json.loads(Path(out + ".meta.json").read_text(encoding="utf-8"))
        assert meta["csv_cells_escaped"] == 2, meta

        for prefix in ("+", "-", "\t", "\r", "\n"):
            assert ow._csv_cell(prefix + "x") == ("'" + prefix + "x", True), repr(prefix)

        out_json = str(Path(td) / "run.json")
        ow.finish_run(
            ratings=[rating], out_path=out_json, fmt="json", engine="playwright",
            tickers_requested=["AAPL"], tickers_completed=["AAPL"], failed_tickers=[],
            blocked=False, remote_api_error=False, allow_empty=False, started_at=0.0,
            extra_meta={"csv_cells_escaped": "caller wins"},
        )
        assert json.loads(Path(out_json).read_text(encoding="utf-8"))[0]["company_name"] == rating.company_name
        meta = json.loads(Path(out_json + ".meta.json").read_text(encoding="utf-8"))
        assert meta["csv_cells_escaped"] == "caller wins", "caller-provided extra_meta must win"


class _CountingSolverClient:
    """Stands in for TwoCaptchaClient: counts purchases, never networks."""

    def __init__(self):
        self.purchases = 0

    def solve_and_wait(self, task):
        self.purchases += 1
        return "fake-token"


_TURNSTILE_BLOCK_HTML = (
    '<html><title>Just a moment...</title>'
    '<div class="cf-turnstile" data-sitekey="0x4AAAAAAAfakeSiteKey"></div></html>'
)


def _count_purchases(mod, block_retries, tickers=("AAPL", "MSFT")):
    """Run one engine's real run() loop with a fake browser whose every
    fetch is blocked by a solvable widget; return the number of purchases."""
    import asyncio as _asyncio

    client = _CountingSolverClient()
    blocked = (None, "blocked")
    saved = {}

    def _patch(name, value):
        saved[name] = getattr(mod, name)
        setattr(mod, name, value)

    try:
        if mod is playwright_scraper:
            class Page:
                url = "https://www.tipranks.com/stocks/aapl/stock-analysis"
                context = None

                def content(self):
                    return _TURNSTILE_BLOCK_HTML

                def evaluate(self, script, *args):
                    return None

            class Browser:
                def new_page(self, **kwargs):
                    return Page()

                def close(self):
                    pass

            class Chromium:
                def launch(self, **kwargs):
                    return Browser()

            class PW:
                chromium = Chromium()

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

            _patch("sync_playwright", lambda: PW())
            _patch("fetch_one", lambda page, ticker: blocked)
            mod.run(list(tickers), client=client, block_retries=block_retries)
        elif mod is selenium_scraper:
            class Driver:
                page_source = _TURNSTILE_BLOCK_HTML
                current_url = "https://www.tipranks.com/stocks/aapl/stock-analysis"

                def execute_script(self, script, *args):
                    return None

                def quit(self):
                    pass

            _patch("webdriver", object())
            _patch("_make_driver", lambda *a, **k: Driver())
            _patch("fetch_one", lambda driver, ticker: blocked)
            mod.run(list(tickers), client=client, block_retries=block_retries)
        elif mod is puppeteer_scraper:
            class Page:
                url = "https://www.tipranks.com/stocks/aapl/stock-analysis"

                async def content(self):
                    return _TURNSTILE_BLOCK_HTML

                async def evaluate(self, script, *args):
                    return None

            class Browser:
                async def newPage(self):
                    return Page()

                async def close(self):
                    pass

            async def _fake_launch(**kwargs):
                return Browser()

            async def _fake_fetch(page, ticker):
                return blocked

            _patch("pyppeteer_launch", object())
            _patch("_launch", _fake_launch)
            _patch("fetch_one_async", _fake_fetch)
            _asyncio.new_event_loop().run_until_complete(
                mod.run_async(list(tickers), client=client, block_retries=block_retries)
            )
        else:
            raise AssertionError(f"unrecognised engine module {mod.__name__}")
    finally:
        for name, value in saved.items():
            setattr(mod, name, value)
    return client.purchases


@check("paid captcha solves: at most one purchase per ticker in every engine, however large --block-retries is, and none when no retry would use the token")
def _():
    for mod in ENGINE_MODULES:
        for retries, expected in ((10, 2), (2, 2), (0, 0)):
            got = _count_purchases(mod, retries)
            assert got == expected, (
                f"{mod.__name__}: --block-retries {retries} bought {got} solves for 2 tickers, expected {expected}"
            )


@check("solve_when_blocked checks the per-ticker budget before buying: a spent budget never reaches the solver")
def _():
    from captcha_solver import SOLVES_PER_TICKER, SolveBudget, solve_when_blocked

    assert SOLVES_PER_TICKER == 1
    client = _CountingSolverClient()
    budget = SolveBudget()
    kwargs = dict(
        client=client, budget=budget, page_url="https://x/", html=_TURNSTILE_BLOCK_HTML,
        count_product_links=lambda _h: 0, extra_markers=("just a moment",),
    )
    assert solve_when_blocked(**kwargs)["action"] == "solved"
    assert solve_when_blocked(**kwargs)["action"] == "skipped_budget_exhausted"
    assert client.purchases == 1


@check("every engine rejects a negative --block-retries and an out-of-range --scraper-api-timeout as bad usage (exit 2, naming the flag); --block-retries 0 is accepted")
def _():
    import contextlib
    import io as _io

    for mod in ENGINE_MODULES:
        for argv, flag in (
            (["--ticker", "AAPL", "--block-retries", "-1"], "--block-retries"),
            (["--ticker", "AAPL", "--scraper-api-timeout", "0"], "--scraper-api-timeout"),
            (["--ticker", "AAPL", "--scraper-api-timeout", "121"], "--scraper-api-timeout"),
        ):
            err = _io.StringIO()
            with contextlib.redirect_stderr(err):
                try:
                    mod.main(argv)
                except SystemExit as exc:
                    code = exc.code
                else:
                    code = None
            assert code == 2, f"{mod.__name__} {argv}: exit {code}"
            assert flag in err.getvalue(), f"{mod.__name__}: error does not name {flag}: {err.getvalue()!r}"
        assert mod.parse_args(["--ticker", "AAPL", "--block-retries", "0"]).block_retries == 0


def _payload_with_renamed_ids():
    payload = _load_fixture("tipranks_aapl_stock_analysis.json")
    for stock in payload["models"]["stocks"]:
        stock["ticker"] = stock.pop("_id")
    return payload


@check("a served payload whose stock list no longer yields the ticker is a parser failure, not 'not found'; an empty stock list is still 'not found'")
def _():
    from tipranks_parser import missing_record_kind

    assert missing_record_kind(_payload_with_renamed_ids()) == "parser_failed"
    assert missing_record_kind({"models": {}}) == "parser_failed"
    assert missing_record_kind({"models": {"stocks": []}}) == "not_found"

    class Response:
        status = 200

        def __init__(self, payload):
            self.payload = payload

        def text(self):
            return json.dumps(self.payload)

    class Page:
        url = "https://www.tipranks.com/stocks/aapl/stock-analysis/payload.json"

        def __init__(self, payload):
            self.payload = payload

        def goto(self, *args, **kwargs):
            return Response(self.payload)

    assert playwright_scraper.fetch_one(Page(_payload_with_renamed_ids()), "AAPL") == (None, "parser_failed")
    assert playwright_scraper.fetch_one(Page({"models": {"stocks": []}}), "AAPL") == (None, "not_found")


@check("every engine: a run where the parser loses every ticker exits 5 and writes nothing; with --allow-empty the sidecar says parser_failed and lists the tickers")
def _():
    import output_writer as ow

    original = scraper_api_client.TwoCaptchaClient.scrape_url
    body = json.dumps(_payload_with_renamed_ids())

    def _fake_scrape_url(self, url, *, data_format="raw", timeout=60, wait_for=None, cdp_url=None):
        return scraper_api_client.ScrapeResult(target_status=200, headers={}, body=body)

    scraper_api_client.TwoCaptchaClient.scrape_url = _fake_scrape_url
    try:
        for mod in ENGINE_MODULES:
            with tempfile.TemporaryDirectory() as td:
                out = Path(td) / "out.json"
                base = ["--ticker", "AAPL", "--twocaptcha-key", "fake-key-for-test-only",
                        "--scraper-api", "--out", str(out)]
                rc = mod.main(base)
                assert rc == ow.EXIT_REMOTE_API_ERROR, f"{mod.__name__}: got {rc}"
                assert not out.exists(), f"{mod.__name__}: wrote output for a parser failure"
                rc = mod.main(base + ["--allow-empty"])
                assert rc == ow.EXIT_REMOTE_API_ERROR, f"{mod.__name__}: got {rc}"
                meta = json.loads(Path(str(out) + ".meta.json").read_text(encoding="utf-8"))
                assert meta["status"] == "parser_failed", meta
                assert meta["parser_failed_tickers"] == ["AAPL"], meta
    finally:
        scraper_api_client.TwoCaptchaClient.scrape_url = original


@check("finish_run: rows plus a parser-failed ticker is partial (6), not complete")
def _():
    import output_writer as ow

    with tempfile.TemporaryDirectory() as td:
        out = str(Path(td) / "run.json")
        code = ow.finish_run(
            ratings=[_dummy_rating()], out_path=out, fmt="json", engine="playwright",
            tickers_requested=["AAPL", "MSFT"], tickers_completed=["AAPL"], failed_tickers=["MSFT"],
            parser_failed_tickers=["MSFT"],
            blocked=False, remote_api_error=False, allow_empty=False, started_at=0.0,
        )
        assert code == ow.EXIT_PARTIAL, code
        meta = json.loads(Path(out + ".meta.json").read_text(encoding="utf-8"))
        assert meta["status"] == "partial" and meta["parser_failed_tickers"] == ["MSFT"], meta


@check("diff_runs.py reports a refusal (missing sidecar) with exit 2, distinct from --fail-on-change's exit 1")
def _():
    import contextlib
    import io as _io

    import diff_runs

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "run.json"
        path.write_text("[]", encoding="utf-8")
        err = _io.StringIO()
        with contextlib.redirect_stderr(err):
            code = diff_runs.main([str(path), str(path)])
        assert code == 2, code
        assert "no sidecar" in err.getvalue()


def _load_ci_checks(root=None):
    import importlib.util

    scanner = ROOT / ".github" / "ci_checks.py"
    if not scanner.exists():
        return None
    spec = importlib.util.spec_from_file_location("tipranks_ci_checks_rules", scanner)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    if root is not None:
        module.ROOT = root
    return module


@check("credential scanner: catches JSON-escaped and bare 32-hex keys, scans .env.* backups, skips any directory holding pyvenv.cfg, and refuses to pass on an empty scan")
def _():
    import subprocess

    if _load_ci_checks() is None:
        return
    key = "0123456789abcdef" * 2
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "tmp" / "clone"
        (root / "tests" / "fixtures").mkdir(parents=True)
        (root / "myenv" / "lib").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / ".gitignore").write_text(".env*\n!.env.example\n", encoding="utf-8")
        escaped = '{"x": "{\\"apiKey\\": \\"' + "Zq9" + "realvalue77" + '\\"}"}'
        (root / "tests" / "fixtures" / "esc.json").write_text(escaped, encoding="utf-8")
        (root / "bare.py").write_text(f'ENDPOINT = "{key}"\n', encoding="utf-8")
        (root / ".env.bak").write_text("TWOCAPTCHA" + "_KEY=" + "Zq9realvalue77\n", encoding="utf-8")
        (root / "myenv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
        (root / "myenv" / "lib" / "vendored.py").write_text(f'api_key = "{key}"\n', encoding="utf-8")
        ci = _load_ci_checks(root)
        findings, read = ci.scan()
        hit = {(path, rule) for path, _line, rule in findings}
        assert ("tests/fixtures/esc.json", "secret_assignment") in hit, findings
        assert ("bare.py", "twocaptcha_key_shape") in hit, findings
        assert any(path == ".env.bak" for path, _r in hit), findings
        assert not any(path.startswith("myenv/") for path, _r in hit), findings
        assert read >= 3, read

    with tempfile.TemporaryDirectory() as td:
        empty = Path(td) / "empty"
        empty.mkdir()
        subprocess.run(["git", "init", "-q", str(empty)], check=True)
        ci = _load_ci_checks(empty)
        assert ci.main() == 1, "an empty scan must not report a pass"


@check("no tracked file cites the private instruction file or its section numbers (state the rule in the repo's own words)")
def _():
    import subprocess

    if not (ROOT / ".git").exists():
        return
    banned = (
        "CLAUDE" + ".md", "claude_" + "2scraper", "2scraper_" + "repo_rules",
        "play" + "book", "\u00a7",
    )
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True,
    ).stdout.split(b"\0")
    hits = []
    for raw in tracked:
        if not raw:
            continue
        rel = raw.decode("utf-8", "surrogateescape")
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for token in banned:
            if token in text:
                hits.append(f"{rel}: {token!r}")
    assert not hits, hits



# --------------------------------------------------------------------------- #
# Found in the 2026-10-19 live test pass
# --------------------------------------------------------------------------- #
@check("scraping_browser_url keeps the endpoint's own login/password/host, sets country/pid, and never echoes a bad endpoint")
def _():
    from scraper_api_client import scraping_browser_url

    base = "ws://abc-zone-scraping_browser-country-us-pid-p1:secretpw@cb.2captcha.com:9222"
    assert scraping_browser_url(base) == base
    assert scraping_browser_url(base, country="de") == "ws://abc-zone-scraping_browser-country-de-pid-p1:secretpw@cb.2captcha.com:9222"
    assert scraping_browser_url("ws://abc-zone-scraping_browser:pw@h:1", profile_id="p9") == "ws://abc-zone-scraping_browser-pid-p9:pw@h:1"
    try:
        scraping_browser_url("ws://plainuser:secretpw@host:9222")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "secretpw" not in str(exc)


@check("user_agent_from reads the live Fingerprint API shape (userAgent.userAgent, then navigator.userAgent)")
def _():
    import fingerprint_client as fc

    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    assert fc.user_agent_from({"userAgent": {"userAgent": ua, "platform": "Windows"}}) == ua
    assert fc.user_agent_from({"userAgent": {"platform": "Windows"}, "navigator": {"userAgent": ua}}) == ua
    assert fc.user_agent_from({"userAgent": ua}) == ua
    assert fc.user_agent_from({"id": 1}) is None


@check("the 'Ignoring --fingerprint' warning is only checked for when --fingerprint was passed")
def _():
    for path in ("playwright_scraper.py", "selenium_scraper.py", "puppeteer_scraper.py"):
        src = (ROOT / path).read_text(encoding="utf-8")
        assert "args.fingerprint and refuse_if_cdp(args.cdp_endpoint)" in src, path


@check("block detection does not depend on the challenge page's language (a ru-RU Chrome shows 'Один момент…')")
def _():
    from tipranks_parser import BLOCK_BODY_MARKERS

    html = "<html><head><title>Один момент…</title></head><body><script>(function(){window._cf_chl_opt = {cvId: '3'};})();</script></body></html>"
    assert any(m in html.lower() for m in BLOCK_BODY_MARKERS)
    normal = '<html><script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script><title>Apple (AAPL)</title></html>'
    assert not any(m in normal.lower() for m in BLOCK_BODY_MARKERS), "a normal page also loads challenge-platform"


@check("proxy_pool refuses a credentialed SOCKS proxy, and no parse error echoes the line (it can carry a password)")
def _():
    import proxy_pool

    for bad in ("socks5://user:secretpass@host.example:1080", "http://user:secretpass@host.example"):
        try:
            proxy_pool.load_proxies(bad, None)
            raise AssertionError(f"expected ProxyParseError for {bad}")
        except proxy_pool.ProxyParseError as exc:
            assert "secretpass" not in str(exc), str(exc)
    assert proxy_pool.load_proxies("socks5://host.example:1080", None)[0].scheme == "socks5"


@check("pyppeteer answers proxy auth through the CDP Fetch domain, not page.authenticate() (Network.setRequestInterception is gone from current Chrome)")
def _():
    src = (ROOT / "puppeteer_scraper.py").read_text(encoding="utf-8")
    assert "await page.authenticate(" not in src
    assert '"Fetch.enable", {"handleAuthRequests": True' in src
    assert '== "Proxy"' in src, "credentials must only answer the proxy's own challenge"


@check("every engine navigates straight to payload.json (the ticker page is Cloudflare-challenged, the payload is not — live 2026-10-19) and classifies 200/400/403 the same way")
def _():
    import asyncio
    from tipranks_parser import ticker_payload_url

    good = _load_fixture("tipranks_aapl_stock_analysis.json")
    challenge = "<html><head><title>Один момент…</title></head><body><script>window._cf_chl_opt={cvId:'3'};</script></body></html>"
    cases = [  # (status, body, expected error_kind)
        (200, json.dumps(good), None),
        (400, '{"msg":"ticker not found"}', "not_found"),
        (403, challenge, "blocked"),
        (200, "not json", "remote_api_error"),
    ]

    class Resp:
        def __init__(self, status, body):
            self.status, self.body = status, body

        def text(self):
            return self.body

    class SyncPage:
        url = "https://www.tipranks.com/x"

        def __init__(self, status, body):
            self.resp, self.visited = Resp(status, body), []

        def goto(self, url, **kwargs):
            self.visited.append(url)
            return self.resp

    class AsyncResp(Resp):
        async def text(self):
            return self.body

    class AsyncPage(SyncPage):
        def __init__(self, status, body):
            self.resp, self.visited = AsyncResp(status, body), []

        async def goto(self, url, *args, **kwargs):
            self.visited.append(url)
            return self.resp

    class Driver:
        current_url = "https://www.tipranks.com/x"

        def __init__(self, status, body):
            self.body, self.visited = body, []
            self.page_source = body if body.startswith("<") else f"<html><body><pre>{body}</pre></body></html>"

        def set_page_load_timeout(self, s):
            pass

        def get(self, url):
            self.visited.append(url)

        def execute_script(self, script):
            return self.body

    for status, body, expected in cases:
        page = SyncPage(status, body)
        rating, kind = playwright_scraper.fetch_one(page, "AAPL")
        assert kind == expected and (rating is not None) == (expected is None), ("playwright", status, kind)
        assert page.visited == [ticker_payload_url("AAPL")], page.visited

        apage = AsyncPage(status, body)
        rating, kind = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            puppeteer_scraper.fetch_one_async(apage, "AAPL"))
        assert kind == expected and (rating is not None) == (expected is None), ("puppeteer", status, kind)
        assert apage.visited == [ticker_payload_url("AAPL")], apage.visited

        driver = Driver(status, body)
        rating, kind = selenium_scraper.fetch_one(driver, "AAPL")
        assert kind == expected and (rating is not None) == (expected is None), ("selenium", status, kind)
        assert driver.visited == [ticker_payload_url("AAPL")], driver.visited


# --------------------------------------------------------------------------- #
# Proxy traffic savers: the pre-check before any browser, media blocking
# through a local proxy (proxy_check.py)
# --------------------------------------------------------------------------- #
import argparse as _argparse  # noqa: E402
import asyncio as _sv_asyncio  # noqa: E402
import contextlib as _contextlib  # noqa: E402
import io as _io  # noqa: E402
import logging as _logging  # noqa: E402
import socket as _socket  # noqa: E402
import threading as _threading  # noqa: E402
from unittest import mock as _mock  # noqa: E402

import proxy_check  # noqa: E402
import proxy_meter  # noqa: E402

_SV_SECRET = "s3cret-Pw-not-real"
_SV_LOGIN = "user-zone-x"


class _StubProxy:
    """A one-port HTTP proxy on 127.0.0.1 that answers every request with a
    fixed status and records the request heads it received."""

    def __init__(self, status):
        self.status, self.heads = status, []
        self.sock = _socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        _threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                self.heads.append(data.decode("latin-1"))
                reason = {407: "Proxy Authentication Required",
                          402: "Payment Required", 200: "OK"}.get(self.status, "X")
                conn.sendall(f"HTTP/1.1 {self.status} {reason}\r\n"
                             "Content-Length: 0\r\nConnection: close\r\n\r\n".encode())

    def url(self):
        # Concatenated, so no line here looks like a credentialled URL.
        return "http://" + _SV_LOGIN + ":" + _SV_SECRET + "@127.0.0.1:" + str(self.port)

    def proxy(self):
        return _proxy_pool.parse_proxy_line(self.url())

    def close(self):
        self.sock.close()


def _closed_port():
    s = _socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@_contextlib.contextmanager
def _captured():
    """stdout, stderr and every log record, as one string."""
    buf = _io.StringIO()
    handler = _logging.StreamHandler(buf)
    root = _logging.getLogger()
    root.addHandler(handler)
    try:
        with _contextlib.redirect_stdout(buf), _contextlib.redirect_stderr(buf):
            yield buf
    finally:
        root.removeHandler(handler)


@check("proxy pre-check: a 407 is auth_failed and fatal, sent as ONE CONNECT to the target host "
       "with the credential; the message names TIPRANKS_PROXY and never prints the login or password")
def _():
    stub = _StubProxy(407)
    try:
        res = proxy_check.precheck(stub.proxy(), "https://www.tipranks.com", timeout=5)
        assert res.outcome == "auth_failed" and res.fatal, res
        assert res.stop_reason == "proxy_auth_failed", res.stop_reason
        assert len(stub.heads) == 1, stub.heads
        head = stub.heads[0]
        assert head.startswith("CONNECT www.tipranks.com:443 "), head[:60]
        assert "Proxy-Authorization: Basic " in head
        pool = _proxy_pool.ProxyPool([stub.proxy()])
        with _captured() as buf:
            gated = proxy_check.gate(pool, "https://www.tipranks.com", timeout=5)
        text = buf.getvalue()
        assert gated is not None and gated.fatal
        assert "TIPRANKS_PROXY" in text and "refused the credential" in text, text
        assert "traffic balance is exhausted" in text, text
        assert _SV_SECRET not in text and _SV_LOGIN not in text, text
        assert pool.next() is pool._proxies[0], "the pre-check must not move the pool's cursor"
    finally:
        stub.close()


@check("proxy pre-check: a 402 is balance_exhausted and fatal, 'traffic/balance exhausted', no credential")
def _():
    stub = _StubProxy(402)
    try:
        res = proxy_check.precheck(stub.proxy(), "https://www.tipranks.com", timeout=5)
        msg = proxy_check.failure_message(res, stub.proxy())
        assert res.outcome == "balance_exhausted" and res.fatal, res
        assert res.stop_reason == "proxy_balance_exhausted"
        assert "traffic/balance exhausted" in msg and _SV_SECRET not in msg and _SV_LOGIN not in msg, msg
    finally:
        stub.close()


@check("proxy pre-check: a plain-http target is one HEAD for its robots.txt; a proxy that does not "
       "answer is a transport error and NOT fatal; no pool or socks5 means no check")
def _():
    stub = _StubProxy(200)
    try:
        res = proxy_check.precheck(stub.proxy(), "http://127.0.0.1:9/x", timeout=5)
        assert res.outcome == "ok" and not res.fatal, res
        assert stub.heads[-1].startswith("HEAD http://127.0.0.1:9/robots.txt "), stub.heads
    finally:
        stub.close()
    dead = _proxy_pool.Proxy("127.0.0.1", _closed_port(), _SV_LOGIN, _SV_SECRET)
    res = proxy_check.precheck(dead, "https://www.tipranks.com", timeout=5)
    assert res.outcome == "transport_error" and not res.fatal, res
    with _captured() as buf:
        assert proxy_check.gate(_proxy_pool.ProxyPool([dead]), "https://www.tipranks.com", timeout=5) is None
    assert _SV_SECRET not in buf.getvalue() and _SV_LOGIN not in buf.getvalue()
    assert proxy_check.gate(None, "https://www.tipranks.com") is None
    socks = _proxy_pool.Proxy("127.0.0.1", 1, scheme="socks5")
    assert proxy_check.precheck(socks, "https://www.tipranks.com").outcome == "skipped"


@check("proxy pre-check: every engine exits 5 on a 407 WITHOUT starting a browser, writes "
       "<out>.latest_attempt.meta.json (failed / proxy_auth_failed), leaves earlier output alone, "
       "and never logs the credential")
def _():
    for mod in ENGINE_MODULES:
        stub = _StubProxy(407)
        launched = []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "out.json")
                Path(out).write_text("[1]")
                Path(out + ".meta.json").write_text("{}")
                with _mock.patch.object(mod, "run", side_effect=lambda *a, **k: launched.append(1)), \
                        _captured() as buf:
                    try:
                        rc = mod.main(["--ticker", "AAPL", "--proxy", stub.url(), "--out", out])
                    except Exception as exc:  # noqa: BLE001 — fail BY NAME
                        rc = f"raised {type(exc).__name__}"
                side = out + ".latest_attempt.meta.json"
                meta = json.loads(Path(side).read_text()) if os.path.exists(side) else {}
                untouched = Path(out).read_text() == "[1]" and Path(out + ".meta.json").read_text() == "{}"
        finally:
            stub.close()
        assert rc == 5 and not launched, f"{mod.__name__}: rc={rc}, browser started={bool(launched)}"
        assert meta.get("status") == "failed" and meta.get("stop_reason") == "proxy_auth_failed", (mod.__name__, meta)
        assert meta.get("rating_count") == 0 and meta.get("tickers_requested") == ["AAPL"], meta
        assert untouched, f"{mod.__name__}: the previous output or its .meta.json was overwritten"
        assert _SV_SECRET not in buf.getvalue() and _SV_LOGIN not in buf.getvalue(), mod.__name__


@check("proxy pre-check: not run over --cdp-endpoint (the local proxy is not used there)")
def _():
    for mod in ENGINE_MODULES:
        with tempfile.TemporaryDirectory() as tmp, \
                _mock.patch.object(proxy_check, "gate") as gate, \
                _mock.patch.object(mod, "run", return_value=([], [], ["AAPL"], [], [])), \
                _captured():
            mod.main(["--ticker", "AAPL", "--proxy", "http://127.0.0.1:1",
                      "--cdp-endpoint", "ws://127.0.0.1:9222", "--out", os.path.join(tmp, "o.json")])
        assert not gate.called, mod.__name__


def _block_media_passed(mod, *argv, env=None):
    seen = {}

    def fake_run(*a, **k):
        seen.update(k)
        return [], [], ["AAPL"], [], []
    with tempfile.TemporaryDirectory() as tmp, \
            _mock.patch.object(mod, "run", side_effect=fake_run), \
            _mock.patch.object(proxy_check, "gate", return_value=None), \
            _mock.patch.dict(os.environ, env or {}), _captured():
        mod.main(["--ticker", "AAPL", "--out", os.path.join(tmp, "o.json"), *argv])
    return seen.get("block_media")


@check("media blocking: on through a local proxy by default, off with --load-media / TIPRANKS_LOAD_MEDIA=1, "
       "off without a proxy and over --cdp-endpoint, forced by the hidden --block-media")
def _():
    default = proxy_check.BLOCK_MEDIA_BY_DEFAULT
    for mod in ENGINE_MODULES:
        name = mod.__name__
        assert _block_media_passed(mod, "--proxy", "http://127.0.0.1:1") is default, name
        assert _block_media_passed(mod, "--proxy", "http://127.0.0.1:1", "--load-media") is False, name
        assert _block_media_passed(mod, "--proxy", "http://127.0.0.1:1",
                                   env={"TIPRANKS_LOAD_MEDIA": "1"}) is False, name
        assert _block_media_passed(mod, "--proxy", "http://127.0.0.1:1",
                                   env={"TIPRANKS_LOAD_MEDIA": "0"}) is default, name
        assert _block_media_passed(mod) is False, name
        assert _block_media_passed(mod, "--proxy", "http://127.0.0.1:1",
                                   "--cdp-endpoint", "ws://127.0.0.1:9222") is False, name
        assert _block_media_passed(mod, "--block-media") is True, name
        with _captured():
            try:
                mod.parse_args(["--ticker", "AAPL", "--load-media", "--block-media"])
                exclusive = False
            except SystemExit:
                exclusive = True
        assert exclusive, f"{name}: --load-media and --block-media must be mutually exclusive"
    parser = _argparse.ArgumentParser()
    proxy_check.add_media_flags(parser)
    assert "--block-media" not in parser.format_help() and "--load-media" in parser.format_help()
    assert env_config.ENV_KEYS.get("TIPRANKS_LOAD_MEDIA") == "load_media"


class _FakeRoute:
    def __init__(self, resource_type):
        self.request = type("R", (), {"resource_type": resource_type})()
        self.verdict = None

    def abort(self):
        self.verdict = "abort"

    def continue_(self):
        self.verdict = "continue"


@check("media blocking, Playwright: route on every URL aborts image/media/font and continues everything "
       "else (document, script, xhr, fetch, stylesheet)")
def _():
    ctx = _mock.MagicMock()
    with _captured():
        proxy_check.install_playwright(ctx)
    pattern, handler = ctx.route.call_args[0]
    assert pattern == "**/*", pattern
    verdicts = {}
    for rtype in ("image", "media", "font", "document", "script", "xhr", "fetch", "stylesheet"):
        route = _FakeRoute(rtype)
        handler(route)
        verdicts[rtype] = route.verdict
    blocked = {"image", "media", "font"}
    assert all(verdicts[t] == ("abort" if t in blocked else "continue") for t in verdicts), verdicts


@check("media blocking, pyppeteer: CDP Fetch on its own session pauses ONLY Image/Media/Font and fails "
       "them; never the removed Network.setRequestInterception")
def _():
    cdp = _mock.MagicMock()
    cdp.send = _mock.AsyncMock()
    page = _mock.MagicMock()
    page.target.createCDPSession = _mock.AsyncMock(return_value=cdp)

    async def drive():
        await proxy_check.install_pyppeteer(page)
        cdp.on.call_args[0][1]({"requestId": "r1", "resourceType": "Image"})
        await _sv_asyncio.sleep(0)
    loop = _sv_asyncio.new_event_loop()
    try:
        with _captured():
            loop.run_until_complete(drive())
    finally:
        loop.close()
    calls = [c[0] for c in cdp.send.await_args_list]
    enable = next((c[1] for c in calls if c[0] == "Fetch.enable"), {})
    kinds = sorted(pt.get("resourceType") for pt in enable.get("patterns", []))
    assert cdp.on.call_args[0][0] == "Fetch.requestPaused" and kinds == ["Font", "Image", "Media"], kinds
    assert ("Fetch.failRequest", {"requestId": "r1", "errorReason": "BlockedByClient"}) in calls, calls
    assert not page.setRequestInterception.called


@check("media blocking, Selenium: CDP Network.setBlockedURLs with image/video/font patterns only; a "
       "driver that refuses CDP is not fatal")
def _():
    driver = _mock.MagicMock()
    with _captured():
        assert proxy_check.install_selenium(driver) is True
    sent = {c[0][0]: c[0][1] for c in driver.execute_cdp_cmd.call_args_list}
    urls = sent["Network.setBlockedURLs"]["urls"]
    assert "*.png" in urls and "*.woff2" in urls and "*.mp4?*" in urls, urls
    assert not [u for u in urls if re.search(r"\.(html?|js|json|css)\b", u)], urls
    broken = _mock.MagicMock()
    broken.execute_cdp_cmd.side_effect = RuntimeError("no CDP")
    with _captured():
        assert proxy_check.install_selenium(broken) is False


def _engine_installs_blocking(mod, block_media):
    """Drive one engine's real run() on fakes; True if blocking went in."""
    blocked = (None, "remote_api_error")
    saved = {}

    def _patch(name, value):
        saved[name] = getattr(mod, name)
        setattr(mod, name, value)
    try:
        with _captured():
            if mod is playwright_scraper:
                ctx = _mock.MagicMock()
                page = _mock.MagicMock(context=ctx)
                browser = _mock.MagicMock()
                browser.new_page.return_value = page
                pw = _mock.MagicMock()
                pw.__enter__.return_value = pw
                pw.chromium.launch.return_value = browser
                _patch("sync_playwright", lambda: pw)
                _patch("fetch_one", lambda page, ticker: blocked)
                mod.run(["AAPL"], block_media=block_media)
                return ctx.route.called
            if mod is selenium_scraper:
                chrome = _mock.MagicMock()
                _patch("webdriver", _mock.MagicMock(Chrome=chrome))
                _patch("Options", _mock.MagicMock)
                _patch("fetch_one", lambda driver, ticker: blocked)
                mod.run(["AAPL"], block_media=block_media)
                return any(c[0][0] == "Network.setBlockedURLs"
                           for c in chrome.return_value.execute_cdp_cmd.call_args_list)
            cdp = _mock.MagicMock()
            cdp.send = _mock.AsyncMock()
            page = _mock.MagicMock()
            page.target.createCDPSession = _mock.AsyncMock(return_value=cdp)
            browser = _mock.MagicMock()
            browser.newPage = _mock.AsyncMock(return_value=page)
            browser.close = _mock.AsyncMock()

            async def fake_launch(**kwargs):
                return browser

            async def fake_fetch(page, ticker):
                return blocked
            _patch("pyppeteer_launch", object())
            _patch("_launch", fake_launch)
            _patch("fetch_one_async", fake_fetch)
            loop = _sv_asyncio.new_event_loop()
            try:
                loop.run_until_complete(mod.run_async(["AAPL"], block_media=block_media))
            finally:
                loop.close()
            return any(c[0][0] == "Fetch.enable" for c in cdp.send.await_args_list)
    finally:
        for name, value in saved.items():
            setattr(mod, name, value)


@check("media blocking: each engine's run() installs it on its page/driver when asked, and not otherwise")
def _():
    for mod in ENGINE_MODULES:
        assert _engine_installs_blocking(mod, True) is True, f"{mod.__name__}: blocking not installed"
        assert _engine_installs_blocking(mod, False) is False, f"{mod.__name__}: blocking installed unasked"



# --------------------------------------------------------------------------- #
# Proxy traffic meter and budget (proxy_meter.py)
# --------------------------------------------------------------------------- #
import contextlib  # noqa: E402
import io  # noqa: E402

REPO_ROOT = str(ROOT)
_MC_FAILED: list = []


def _mc(label, condition):
    """One sub-assertion of a meter check; failures are collected by name."""
    if not condition:
        _MC_FAILED.append(label)
    return bool(condition)


def _mgroup(title):
    pass


def _run_meter_group(fn, *args):
    del _MC_FAILED[:]
    with _captured():
        fn(*args)
    assert not _MC_FAILED, "; ".join(_MC_FAILED)


def ProxyPool(entries):  # noqa: N802 — what start() is given: a truthy pool
    return list(entries)




def _meter_rating():
    from tipranks_parser import parse_payload
    payload = _load_fixture("tipranks_aapl_stock_analysis.json")
    return parse_payload(payload, "AAPL", source_url="x", scraped_at="x")


def _meter_rows():
    return [_meter_rating()]


def _meter_finish(rows, out, stop_reason, proxy_extra):
    from output_writer import finish_run
    return finish_run(
        ratings=rows, out_path=out, fmt="json", engine="playwright",
        tickers_requested=["AAPL", "MSFT"],
        tickers_completed=["AAPL"] if rows else [],
        failed_tickers=[] if stop_reason == "completed" and rows else ["MSFT"],
        blocked=False, remote_api_error=False, allow_empty=False,
        started_at=0.0, extra_meta=dict(proxy_extra) if proxy_extra else None,
        budget_exceeded=stop_reason == "proxy_budget_exceeded")


def _meter_main(mod, argv, run_effect):
    """main() with run() faked; returns (rc, kwargs run got, meta, attempt, log)."""
    seen = {}

    def fake_run(*a, **k):
        seen.update(k)
        return run_effect(k)
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "o.json")
        with _mock.patch.object(mod, "run", side_effect=fake_run), \
                _mock.patch.object(proxy_check, "gate", return_value=None), \
                _captured() as buf:
            rc = mod.main(["--ticker", "AAPL", "--ticker", "MSFT", "--out", out, *argv])
        meta = json.loads(Path(out + ".meta.json").read_text()) if os.path.exists(out + ".meta.json") else {}
        side = out + ".latest_attempt.meta.json"
        attempt = json.loads(Path(side).read_text()) if os.path.exists(side) else {}
    return rc, seen, meta, attempt, buf.getvalue()


def _meter_run_loop(mod, meter):
    """The engine's real run() loop on a fake browser: the first ticker
    spends past the budget, and the loop must stop before the second."""
    calls = []
    rating = _meter_rating()

    def fake_fetch(page, ticker):
        calls.append(ticker)
        meter.add(20_000)
        return rating, None

    async def fake_fetch_async(page, ticker):
        return fake_fetch(page, ticker)

    name = mod.__name__
    with contextlib.ExitStack() as stack:
        stack.enter_context(_captured())
        if name == "playwright_scraper":
            stack.enter_context(_mock.patch.object(mod, "sync_playwright"))
            stack.enter_context(_mock.patch.object(mod, "_launch"))
            stack.enter_context(_mock.patch.object(mod, "fetch_one", side_effect=fake_fetch))
        elif name == "puppeteer_scraper":
            page = _mock.MagicMock()
            cdp = _mock.MagicMock()
            cdp.send = _mock.AsyncMock()
            page.target.createCDPSession = _mock.AsyncMock(return_value=cdp)
            browser = _mock.MagicMock()
            browser.newPage = _mock.AsyncMock(return_value=page)
            browser.close = _mock.AsyncMock()
            stack.enter_context(_mock.patch.object(mod, "pyppeteer_launch", object()))
            stack.enter_context(_mock.patch.object(mod, "_launch", _mock.AsyncMock(return_value=browser)))
            stack.enter_context(_mock.patch.object(mod, "fetch_one_async", side_effect=fake_fetch_async))
        else:
            stack.enter_context(_mock.patch.object(mod, "webdriver", _mock.MagicMock()))
            stack.enter_context(_mock.patch.object(mod, "_make_driver"))
            stack.enter_context(_mock.patch.object(mod, "fetch_one", side_effect=fake_fetch))
        try:
            result = mod.run(["AAPL", "MSFT", "GOOGL"], meter=meter)
        except BaseException as exc:  # noqa: BLE001 — fail BY NAME
            result = f"raised {type(exc).__name__}"
    return result, calls


@check("proxy meter: counting, the budget flag and env, CDP/Playwright/Selenium/pyppeteer/HTTP byte "
       "accounting against known sizes, and the sidecars (proxy_bytes / proxy_budget_bytes / "
       "proxy_budget_exceeded in .meta.json and .latest_attempt.meta.json; exit 6 with rows, 5 without)")
def _():
    _run_meter_group(test_proxy_meter_units, [])


@check("proxy meter in a real browser (opt-in: PROXY_METER_BROWSER=1 with a Chromium)")
def _():
    _run_meter_group(test_proxy_meter_in_a_real_browser, [])


@check("proxy meter, every engine: main() meters only through a local proxy (not without one, not over "
       "--cdp-endpoint) and passes the budget to run()")
def _():
    for mod in ENGINE_MODULES:
        name = mod.__name__
        empty = lambda k: ([], [], ["AAPL", "MSFT"], [], [])  # noqa: E731
        _, seen, _, _, _ = _meter_main(mod, ["--proxy", "http://127.0.0.1:1"], empty)
        meter = seen.get("meter")
        assert isinstance(meter, proxy_meter.ProxyMeter), f"{name}: no meter through a proxy"
        assert meter.budget_bytes == 10_000_000, f"{name}: default budget {meter.budget_bytes}"
        _, seen, _, _, _ = _meter_main(mod, ["--proxy", "http://127.0.0.1:1", "--proxy-budget-mb", "2"], empty)
        assert seen["meter"].budget_bytes == 2_000_000, name
        with _mock.patch.dict(os.environ, {"TIPRANKS_PROXY_BUDGET_MB": "3"}):
            _, seen, _, _, _ = _meter_main(mod, ["--proxy", "http://127.0.0.1:1"], empty)
        assert seen["meter"].budget_bytes == 3_000_000, f"{name}: TIPRANKS_PROXY_BUDGET_MB ignored"
        _, seen, _, _, _ = _meter_main(mod, [], empty)
        assert seen.get("meter") is None, f"{name}: metered without a proxy"
        _, seen, _, _, _ = _meter_main(mod, ["--proxy", "http://127.0.0.1:1",
                                             "--cdp-endpoint", "ws://127.0.0.1:9222"], empty)
        assert seen.get("meter") is None, f"{name}: metered over --cdp-endpoint"
        with _captured():
            try:
                mod.parse_args(["--ticker", "AAPL", "--proxy-budget-mb", "-1"])
                refused = False
            except SystemExit as exc:
                refused = exc.code == 2
        assert refused, f"{name}: --proxy-budget-mb -1 must be a usage error"


@check("proxy meter, every engine: budget crossed on the first of three tickers -> the real run() loop "
       "stops before the second, keeps the first rating, and marks the rest failed")
def _():
    for mod in ENGINE_MODULES:
        meter = proxy_meter.ProxyMeter(0.01)
        result, calls = _meter_run_loop(mod, meter)
        assert calls == ["AAPL"], f"{mod.__name__}: fetched {calls} (result {result})"
        ratings, completed, failed, blocked, parser_failed = result
        assert len(ratings) == 1 and completed == ["AAPL"], (mod.__name__, completed)
        assert failed == ["MSFT", "GOOGL"], (mod.__name__, failed)


@check("proxy meter, every engine: over budget -> exit 6 with the rows kept (5 with none), stop_reason "
       "proxy_budget_exceeded, the proxy fields in both sidecars, and 'Proxy traffic: X.X MB of Y MB "
       "budget' in the log")
def _():
    rating = _meter_rating()

    def spent(rows):
        def effect(k):
            k["meter"].add(20_000_000)
            if rows:
                return [rating], ["AAPL"], ["MSFT"], [], []
            return [], [], ["AAPL", "MSFT"], [], []
        return effect

    for mod in ENGINE_MODULES:
        name = mod.__name__
        rc, _, meta, attempt, log_text = _meter_main(mod, ["--proxy", "http://127.0.0.1:1"], spent(True))
        assert rc == 6, f"{name}: rc={rc}"
        assert meta.get("status") == "partial" and meta.get("stop_reason") == "proxy_budget_exceeded", (name, meta)
        assert meta.get("proxy_bytes") == 20_000_000 and meta.get("proxy_budget_exceeded") is True, (name, meta)
        assert meta.get("proxy_budget_bytes") == 10_000_000 and attempt.get("proxy_bytes") == 20_000_000, name
        assert "Proxy traffic: 20.0 MB of 10.0 MB budget" in log_text, f"{name}: no summary line"
        rc, _, meta, attempt, _ = _meter_main(mod, ["--proxy", "http://127.0.0.1:1"], spent(False))
        assert rc == 5 and not meta, f"{name}: rc={rc}, meta={meta}"
        assert attempt.get("stop_reason") == "proxy_budget_exceeded" and attempt.get("status") == "failed", attempt
        rc, _, meta, attempt, _ = _meter_main(
            mod, [], lambda k: ([rating], ["AAPL", "MSFT"], [], [], []))
        assert rc == 0 and "proxy_bytes" not in meta and not attempt, f"{name}: an unmetered run changed"


@check("proxy meter, every engine: installed where the browser starts (Playwright context events + "
       "route, pyppeteer CDP Network domain, Selenium performance log + CDP blocking)")
def _():
    meter = proxy_meter.ProxyMeter(10)
    with _captured():
        # Playwright: the context of the page run() opens.
        with _mock.patch.object(playwright_scraper, "sync_playwright"), \
                _mock.patch.object(playwright_scraper, "_launch") as launch, \
                _mock.patch.object(playwright_scraper, "fetch_one", return_value=(None, "not_found")):
            playwright_scraper.run(["AAPL"], meter=meter)
        ctx = launch.return_value.new_page.return_value.context
        assert any(c[0][0] == "requestfinished" for c in ctx.on.call_args_list), "Playwright: not metered"
        # pyppeteer
        page = _mock.MagicMock()
        cdp = _mock.MagicMock()
        cdp.send = _mock.AsyncMock()
        page.target.createCDPSession = _mock.AsyncMock(return_value=cdp)
        browser = _mock.MagicMock()
        browser.newPage = _mock.AsyncMock(return_value=page)
        browser.close = _mock.AsyncMock()

        async def not_found(page, ticker):
            return None, "not_found"
        with _mock.patch.object(puppeteer_scraper, "pyppeteer_launch", object()), \
                _mock.patch.object(puppeteer_scraper, "_launch", _mock.AsyncMock(return_value=browser)), \
                _mock.patch.object(puppeteer_scraper, "fetch_one_async", side_effect=not_found):
            puppeteer_scraper.run(["AAPL"], meter=meter)
        assert ("Network.enable", {}) in [c[0] for c in cdp.send.await_args_list], "pyppeteer: not metered"
        # Selenium: the driver _make_driver builds.
        fake_wd = _mock.MagicMock()
        with _mock.patch.object(selenium_scraper, "webdriver", fake_wd), \
                _mock.patch.object(selenium_scraper, "Options") as options:
            driver = selenium_scraper._make_driver(True, None, meter=meter)
        assert isinstance(getattr(driver, "_proxy_meter_counter", None), proxy_meter.CdpCounter), \
            "Selenium: not metered"
        caps = [c[0] for c in options.return_value.set_capability.call_args_list]
        assert ("goog:loggingPrefs", {"performance": "ALL"}) in caps, "Selenium: no performance log"
        with _mock.patch.object(selenium_scraper, "webdriver", _mock.MagicMock()), \
                _mock.patch.object(selenium_scraper, "Options"):
            plain = selenium_scraper._make_driver(True, None, meter=None)
        assert not isinstance(getattr(plain, "_proxy_meter_counter", None), proxy_meter.CdpCounter), \
            "Selenium: metered without a meter"


@check("canary: the proxied run passes --proxy-budget-mb 10, puts its proxy traffic in the step "
       "summary, and fails with ::error::stopped: proxy budget 10 MB exceeded over budget")
def _():
    _run_meter_group(test_canary_reports_proxy_traffic)




# ---------------------------------------------------------------------------
# Proxy traffic meter and budget
# ---------------------------------------------------------------------------

class _ByteCountingProxy:
    """A forward HTTP proxy on 127.0.0.1 that serves every plain-http GET
    itself with a body of a known size, and counts every byte it received
    and sent. The meter is judged against these two numbers."""

    def __init__(self, sizes):
        import socket
        import threading
        self.sizes = dict(sizes)            # path -> body length
        self.received = self.sent = 0
        # CONNECTs are the browser's own background connections (a system
        # Chromium phones home through its proxy too); they are not page
        # requests, this stub refuses them, and they are kept apart.
        self.connects = 0
        self.paths = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(32)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        import threading
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(conn,), daemon=True).start()

    def _one(self, conn):
        from urllib.parse import urlparse
        with conn:
            buf = b""
            while True:
                while b"\r\n\r\n" not in buf:
                    try:
                        chunk = conn.recv(65536)
                    except OSError:
                        return
                    if not chunk:
                        return
                    buf += chunk
                head, _, buf = buf.partition(b"\r\n\r\n")
                lines = head.decode("latin-1").split("\r\n")
                if lines[0].startswith("CONNECT "):
                    self.connects += 1
                    try:
                        conn.sendall(b"HTTP/1.1 403 Forbidden\r\n"
                                     b"Content-Length: 0\r\n\r\n")
                    except OSError:
                        pass
                    return
                self.received += len(head) + 4
                length = 0
                for line in lines[1:]:
                    if line.lower().startswith("content-length:"):
                        length = int(line.split(":", 1)[1])
                while len(buf) < length:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    buf += chunk
                self.received += length
                buf = buf[length:]
                path = urlparse(lines[0].split(" ")[1]).path or "/"
                self.paths.append(path)
                size = self.sizes.get(path, 0)
                ctype = ("text/html" if path == "/" else "text/css"
                         if path.endswith(".css") else
                         "application/javascript" if path.endswith(".js")
                         else "application/octet-stream")
                body = (b"<html><head><link rel=stylesheet href=/s.css>"
                        b"<script src=/a.js></script></head><body><p id=t>"
                        if path == "/" else b"")
                body = body + b"x" * max(0, size - len(body))
                if path == "/":
                    body = body[:-11] + b"</p></body>"
                status = b"200 OK" if path in self.sizes else b"404 Not Found"
                out = (b"HTTP/1.1 " + status + b"\r\nContent-Type: "
                       + ctype.encode() + b"\r\nContent-Length: "
                       + str(len(body)).encode()
                       + b"\r\nCache-Control: no-store\r\n\r\n" + body)
                try:
                    conn.sendall(out)
                except OSError:
                    return
                self.sent += len(out)

    @property
    def total(self):
        return self.received + self.sent

    def url(self):
        return "http://127.0.0.1:" + str(self.port)

    def close(self):
        self.sock.close()


def _base_exception(fn):
    try:
        fn()
    except BaseException as e:  # noqa: BLE001 — the point is to see it
        return e
    return None


def test_proxy_meter_units(skips):
    _mgroup("Proxy traffic meter: counting, the budget, the sidecar")
    import argparse
    from unittest import mock
    import output_writer
    import proxy_meter
    ok = True
    ns = argparse.Namespace

    # -- the meter itself ----------------------------------------------------
    m = proxy_meter.ProxyMeter(1)
    fired = []
    m.on_exceed(lambda: fired.append(1))
    m.add(600_000)
    m.add(400_000)
    ok &= _mc("1 MB budget: exactly 1,000,000 bytes is not over it",
                m.bytes == 1_000_000 and not m.exceeded and not fired)
    m.add(1)
    ok &= _mc("...one byte more is, and the refusal hook fires",
                m.exceeded and fired == [1])
    m.add(50)
    ok &= _mc("...the hook fires once, not per request", fired == [1])
    err = _base_exception(m.check)
    ok &= _mc("_mc() raises ProxyBudgetExceeded once over budget",
                isinstance(err, proxy_meter.ProxyBudgetExceeded))
    ok &= _mc("ProxyBudgetExceeded passes an `except Exception` (a retry "
                "loop cannot swallow it)",
                not issubclass(proxy_meter.ProxyBudgetExceeded, Exception))
    ok &= _mc("sidecar fields: bytes, budget in bytes, exceeded",
                m.sidecar() == {"proxy_bytes": 1_000_051,
                                "proxy_budget_bytes": 1_000_000,
                                "proxy_budget_exceeded": True})
    ok &= _mc(f"summary line ({m.summary()})",
                m.summary() == "Proxy traffic: 1.0 MB of 1.0 MB budget")
    late = []
    m.on_exceed(lambda: late.append(1))
    ok &= _mc("a hook added after the cap was crossed runs at once",
                late == [1])
    free = proxy_meter.ProxyMeter(0)
    free.add(10 ** 12)
    ok &= _mc("--proxy-budget-mb 0 means no cap (null budget in the "
                "sidecar)", not free.exceeded and _base_exception(free.check)
                is None and free.sidecar()["proxy_budget_bytes"] is None)

    # -- the flag and the environment ----------------------------------------
    parser = argparse.ArgumentParser()
    proxy_meter.add_budget_flag(parser)
    for bad in ("-1", "abc", "nan", "inf"):
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                parser.parse_args(["--proxy-budget-mb", bad])
            refused = False
        except SystemExit as e:
            refused = e.code == 2
        ok &= _mc(f"--proxy-budget-mb {bad} is a usage error (exit 2)",
                    refused)
    ok &= _mc("--proxy-budget-mb 0 and 2.5 parse",
                parser.parse_args(["--proxy-budget-mb", "0"]).proxy_budget_mb
                == 0.0 and parser.parse_args(
                    ["--proxy-budget-mb", "2.5"]).proxy_budget_mb == 2.5)
    env = proxy_meter.BUDGET_ENV
    with mock.patch.dict(os.environ, {env: ""}):
        ok &= _mc("default budget is 10 MB",
                    proxy_meter.budget_mb(ns(proxy_budget_mb=None)) == 10.0
                    and proxy_meter.DEFAULT_BUDGET_MB == 10.0)
    with mock.patch.dict(os.environ, {env: "2.5"}):
        ok &= _mc(f"{env} sets the budget",
                    proxy_meter.budget_mb(ns(proxy_budget_mb=None)) == 2.5)
        ok &= _mc("...and the flag wins over it",
                    proxy_meter.budget_mb(ns(proxy_budget_mb=1.0)) == 1.0)
    with mock.patch.dict(os.environ, {env: "lots"}):
        ok &= _mc(f"an unparseable {env} stops the run instead of "
                    "silently using a default",
                    isinstance(_base_exception(lambda: proxy_meter.budget_mb(
                        ns(proxy_budget_mb=None))), SystemExit))
    if hasattr(env_config, "ENV_KEYS"):
        ok &= _mc(f"{env} maps onto --proxy-budget-mb",
                    env_config.ENV_KEYS.get(env) == "proxy_budget_mb")
    pool = ProxyPool(["http://127.0.0.1:1"])
    ok &= _mc("metered only through a local proxy: not without one, not "
                "over --cdp-endpoint",
                proxy_meter.start(ns(proxy_budget_mb=None), None) is None
                and proxy_meter.start(ns(proxy_budget_mb=None), pool,
                                      remote=True) is None
                and isinstance(proxy_meter.start(ns(proxy_budget_mb=None),
                                                 pool), proxy_meter.ProxyMeter))

    a = ns(proxy_budget_mb=0.001)
    proxy_meter.start(a, pool).add(5_000)
    ok &= _mc("proxy_meter.check(args) stops a run that is over budget",
                isinstance(_base_exception(lambda: proxy_meter.check(a)),
                           proxy_meter.ProxyBudgetExceeded)
                and _base_exception(lambda: proxy_meter.check(
                    ns(proxy_budget_mb=None))) is None)

    # -- CDP events with known sizes (pyppeteer, Selenium) ------------------
    m = proxy_meter.ProxyMeter(0)
    c = proxy_meter.CdpCounter(m)
    req = {"method": "GET", "url": "http://x.test/a", "headers": {"A": "b"}}
    est = len("GET http://x.test/a HTTP/1.1\r\n") + 2 + len("A: b\r\n")
    c.feed("Network.requestWillBeSent", {"requestId": "1", "request": req})
    c.feed("Network.dataReceived", {"requestId": "1", "encodedDataLength": 99})
    c.feed("Network.loadingFinished", {"requestId": "1",
                                       "encodedDataLength": 1234})
    ok &= _mc(f"CDP: a finished request = its request + encodedDataLength "
                f"({m.bytes} vs {est + 1234})", m.bytes == est + 1234)
    c.feed("Network.requestWillBeSent", {"requestId": "2", "request": req})
    c.feed("Network.loadingFailed", {"requestId": "2",
                                     "errorText": "net::ERR_BLOCKED_BY_CLIENT"})
    c.feed("Network.requestWillBeSent", {"requestId": "2b", "request": req})
    c.feed("Network.loadingFailed", {"requestId": "2b", "errorText": "x",
                                     "blockedReason": "inspector"})
    ok &= _mc("CDP: a request the browser blocked costs nothing",
                m.bytes == est + 1234)
    c.feed("Network.requestWillBeSent", {"requestId": "3", "request": req})
    c.feed("Network.dataReceived", {"requestId": "3", "encodedDataLength": 50})
    c.feed("Network.loadingFailed", {"requestId": "3",
                                     "errorText": "net::ERR_CONNECTION_RESET"})
    ok &= _mc("CDP: a request that failed mid-body costs what arrived",
                m.bytes == 2 * est + 1234 + 50)
    c.feed("Network.requestWillBeSent", {"requestId": "4", "request": req})
    c.feed("Network.requestWillBeSent", {
        "requestId": "4", "request": req,
        "redirectResponse": {"encodedDataLength": 300}})
    c.feed("Network.loadingFinished", {"requestId": "4",
                                       "encodedDataLength": 500})
    ok &= _mc("CDP: a redirect hop is counted as its own exchange",
                m.bytes == 4 * est + 1234 + 50 + 300 + 500)

    # -- Playwright: sizes() of every finished/failed request ---------------
    m = proxy_meter.ProxyMeter(0.001)
    ctx = mock.MagicMock()
    proxy_meter.install_playwright(ctx, m)
    on = {call[0][0]: call[0][1] for call in ctx.on.call_args_list}
    request = mock.MagicMock()
    request.sizes.return_value = {"requestHeadersSize": 100,
                                  "requestBodySize": 20,
                                  "responseHeadersSize": 200,
                                  "responseBodySize": 500}
    on.get("requestfinished", lambda r: None)(request)
    dead = mock.MagicMock()
    dead.sizes.side_effect = RuntimeError("no response")
    on.get("requestfailed", lambda r: None)(dead)
    ok &= _mc(f"Playwright: request.sizes() summed on requestfinished "
                f"({m.bytes} of 820), a failed one without sizes is 0",
                m.bytes == 820 and "requestfailed" in on)
    pattern, gate = ctx.route.call_args[0]
    route = mock.MagicMock()
    gate(route)
    ok &= _mc(f"Playwright: under budget the route falls through to the "
                f"next handler ({pattern})", pattern == "**/*"
                and route.fallback.called and not route.abort.called)
    on["requestfinished"](request)
    route = mock.MagicMock()
    gate(route)
    ok &= _mc("Playwright: over budget every new request is aborted",
                route.abort.called and not route.fallback.called)
    ok &= _mc("no meter, nothing installed",
                proxy_meter.install_playwright(ctx, None) is None
                and ctx.route.call_count == 1)

    # -- Selenium: the performance log --------------------------------------
    m = proxy_meter.ProxyMeter(0.001)
    driver = mock.MagicMock()
    log = [{"message": json.dumps({"message": {
        "method": "Network.requestWillBeSent",
        "params": {"requestId": "9", "request": req}}})},
        {"message": json.dumps({"message": {
            "method": "Network.loadingFinished",
            "params": {"requestId": "9", "encodedDataLength": 2000}}})},
        {"message": "not json"}]
    driver.get_log.return_value = log
    proxy_meter.install_selenium(driver, m)
    entries = proxy_meter.selenium_log(driver)
    ok &= _mc(f"Selenium: the performance log is counted ({m.bytes} = "
                f"{est + 2000}) and handed back to the engine's own reader",
                m.bytes == est + 2000 and entries == log)
    blocked = [call[0] for call in driver.execute_cdp_cmd.call_args_list]
    ok &= _mc("Selenium: over budget, every URL is blocked over CDP",
                ("Network.setBlockedURLs", {"urls": ["*"]}) in blocked)
    options = mock.MagicMock()
    proxy_meter.selenium_options(options, m)
    ok &= _mc("Selenium: the performance log is requested",
                options.set_capability.call_args[0] ==
                ("goog:loggingPrefs", {"performance": "ALL"}))

    # -- pyppeteer: its own CDP session -------------------------------------
    import asyncio
    m = proxy_meter.ProxyMeter(0.001)
    cdp = mock.MagicMock()
    cdp.send = mock.AsyncMock()
    page = mock.MagicMock()
    page.target.createCDPSession = mock.AsyncMock(return_value=cdp)

    async def _drive():
        await proxy_meter.install_pyppeteer(page, m)
        on = {call[0][0]: call[0][1] for call in cdp.on.call_args_list}
        on["Network.requestWillBeSent"]({"requestId": "p", "request": req})
        on["Network.loadingFinished"]({"requestId": "p",
                                       "encodedDataLength": 1500})
        await asyncio.sleep(0)

    asyncio.run(_drive())
    sent = [call[0] for call in cdp.send.await_args_list]
    ok &= _mc(f"pyppeteer: Network events counted ({m.bytes} = "
                f"{est + 1500})", m.bytes == est + 1500
                and ("Network.enable", {}) in sent)
    ok &= _mc("pyppeteer: over budget, every URL is blocked over CDP",
                ("Network.setBlockedURLs", {"urls": ["*"]}) in sent)

    # -- an HTTP client, through a real stub proxy with known sizes ---------
    import requests
    stub = _ByteCountingProxy({"/": 20_000})
    try:
        m = proxy_meter.ProxyMeter(0)
        r = requests.get("http://meter.test/", proxies={"http": stub.url()},
                         timeout=10)
        proxy_meter.count_response(m, r)
        import time as _time
        for _ in range(50):
            if stub.sent:
                break
            _time.sleep(0.02)
        drift = abs(m.bytes - stub.total) / stub.total
        ok &= _mc(f"HTTP client through a stub proxy: metered {m.bytes} B, "
                    f"the proxy carried {stub.total} B ({drift:.1%}, "
                    f"tolerance 5%)", drift <= 0.05)
    finally:
        stub.close()

    # -- the sidecars --------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "out")
        metered = proxy_meter.ProxyMeter(10)
        metered.add(123_456)
        rows = _meter_rows()
        with contextlib.redirect_stdout(io.StringIO()):
            rc = _meter_finish(rows, out, "completed", metered.sidecar())
        meta = json.load(open(out + ".meta.json"))
        attempt = json.load(open(out + ".latest_attempt.meta.json"))
        ok &= _mc(f"a metered run records proxy_bytes / proxy_budget_bytes "
                    f"/ proxy_budget_exceeded in .meta.json (rc={rc})",
                    rc == 0 and meta.get("proxy_bytes") == 123_456
                    and meta.get("proxy_budget_bytes") == 10_000_000
                    and meta.get("proxy_budget_exceeded") is False)
        ok &= _mc("...and the same in .latest_attempt.meta.json",
                    attempt.get("proxy_bytes") == 123_456
                    and attempt.get("status") == meta.get("status"))

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "out")
        over = proxy_meter.ProxyMeter(0.0001)
        over.add(5_000)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = _meter_finish(_meter_rows(), out,
                               proxy_meter.STOP_BUDGET_EXCEEDED, over.sidecar())
        meta = json.load(open(out + ".meta.json"))
        ok &= _mc(f"over budget WITH rows: exit 6, partial, "
                    f"stop_reason proxy_budget_exceeded (rc={rc})",
                    rc == 6 and meta.get("status") == "partial"
                    and meta.get("stop_reason") == "proxy_budget_exceeded"
                    and meta.get("proxy_budget_exceeded") is True)

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "out")
        for name, body in (("out.json", "[1]"), ("out.meta.json", "{}")):
            with open(os.path.join(tmp, name), "w") as f:
                f.write(body)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = _meter_finish([], out, proxy_meter.STOP_BUDGET_EXCEEDED,
                               over.sidecar())
        attempt = json.load(open(out + ".latest_attempt.meta.json"))
        untouched = (open(out + ".json").read() == "[1]"
                     and open(out + ".meta.json").read() == "{}")
        ok &= _mc(f"over budget with NO rows: exit 5 (rc={rc}), the "
                    f"attempt sidecar says so, the previous output is kept",
                    rc == 5 and untouched
                    and attempt.get("stop_reason") == "proxy_budget_exceeded"
                    and attempt.get("proxy_budget_exceeded") is True
                    and attempt.get("status") == "failed")

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "out")
        with contextlib.redirect_stdout(io.StringIO()):
            _meter_finish(_meter_rows(), out, "completed", None)
        ok &= _mc("an unmetered run writes no attempt sidecar and no "
                    "proxy fields (unchanged)",
                    not os.path.exists(out + ".latest_attempt.meta.json")
                    and "proxy_bytes" not in json.load(
                        open(out + ".meta.json")))

    a = ns(proxy_budget_mb=None)
    setattr(a, "_proxy_meter", metered)
    # Read off the logger calls, not a handler: the root logger's level
    # differs between entry points and would hide an INFO line.
    with mock.patch.object(proxy_meter.logger, "info") as info, \
            mock.patch.object(proxy_meter.logger, "warning") as warn:
        extra = proxy_meter.finish(a, {"site": 1})
    said = [c[0][0] % c[0][1:] for c in
            info.call_args_list + warn.call_args_list]
    ok &= _mc(f"the run ends with 'Proxy traffic: X.X MB of Y MB budget' "
                f"({said})",
                "Proxy traffic: 0.1 MB of 10.0 MB budget" in said
                and extra.get("site") == 1 and extra.get("proxy_bytes"))
    return ok


# A request the PAGE makes after the cap. Navigations are stopped by the
# engines' own check() before they are issued; this is what the in-browser
# refusal (a route abort, CDP Network.setBlockedURLs) is for.
_LATE_FETCH = ("fetch('/n').then(r => r.status).catch(e => 'refused')")


def test_proxy_meter_in_a_real_browser(skips):
    """Opt-in (PROXY_METER_BROWSER=1): each installed driver loads a page
    through _ByteCountingProxy, and the meter must agree with what the proxy
    carried. Needs a Chromium; not run in CI, which installs none."""
    _mgroup("Proxy traffic meter in a real browser (opt-in)")
    if os.environ.get("PROXY_METER_BROWSER") != "1":
        print("  not run — set PROXY_METER_BROWSER=1 with a Chromium "
              "installed to measure the meter against a stub proxy")
        return True
    import proxy_meter
    import time as _time
    ok = True
    sizes = {"/": 60_000, "/s.css": 30_000, "/a.js": 90_000, "/n": 40_000}
    chromium = os.environ.get("PROXY_METER_CHROMIUM", "/usr/bin/chromium")
    target = "http://meter.test/"
    ran = 0
    for driver in ("playwright", "pyppeteer", "selenium"):
        try:
            __import__(driver)
        except ImportError:
            continue
        ran += 1
        stub = _ByteCountingProxy(sizes)
        meter = proxy_meter.ProxyMeter(0.15)
        stopped = None
        try:
            if driver == "playwright":
                from playwright.sync_api import sync_playwright
                with sync_playwright() as pw:
                    b = pw.chromium.launch(headless=True,
                                           proxy={"server": stub.url()})
                    ctx = b.new_context()
                    proxy_meter.install_playwright(ctx, meter)
                    p = ctx.new_page()
                    p.goto(target)
                    p.wait_for_timeout(800)
                    first = (meter.bytes, stub.total)
                    p.evaluate(_LATE_FETCH)
                    p.wait_for_timeout(500)
                    stopped = _base_exception(meter.check)
                    b.close()
            elif driver == "pyppeteer":
                import asyncio
                from pyppeteer import launch

                async def go():
                    b = await launch(headless=True, executablePath=chromium,
                                     args=["--no-sandbox",
                                           f"--proxy-server={stub.url()}"],
                                     handleSIGINT=False, handleSIGTERM=False,
                                     handleSIGHUP=False)
                    p = await b.newPage()
                    await proxy_meter.install_pyppeteer(p, meter)
                    await p.goto(target)
                    await asyncio.sleep(0.8)
                    snap = (meter.bytes, stub.total)
                    await p.evaluate(_LATE_FETCH, force_expr=True)
                    await asyncio.sleep(0.5)
                    await b.close()
                    return snap
                first = asyncio.new_event_loop().run_until_complete(go())
                stopped = _base_exception(meter.check)
            else:
                from selenium import webdriver
                from selenium.webdriver.chrome.options import Options
                from selenium.webdriver.chrome.service import Service
                o = Options()
                o.add_argument("--headless=new")
                o.add_argument("--no-sandbox")
                o.add_argument(f"--proxy-server={stub.url()}")
                o.binary_location = chromium
                proxy_meter.selenium_options(o, meter)
                d = webdriver.Chrome(options=o, service=Service(
                    os.environ.get("PROXY_METER_CHROMEDRIVER",
                                   "/usr/bin/chromedriver")))
                proxy_meter.install_selenium(d, meter)
                d.get(target)
                _time.sleep(0.8)
                proxy_meter.drain_selenium(d)
                first = (meter.bytes, stub.total)
                d.execute_script("return " + _LATE_FETCH)
                _time.sleep(0.5)
                proxy_meter.drain_selenium(d)
                stopped = _base_exception(meter.check)
                d.quit()
        except Exception as e:  # noqa: BLE001 — report by name
            ok &= _mc(f"{driver}: real-browser meter run ({e!r:.160})", False)
            stub.close()
            continue
        stub.close()
        metered, carried = first
        drift = abs(metered - carried) / max(1, carried)
        ok &= _mc(f"{driver}: page + css + js through the stub proxy: "
                    f"metered {metered} B, the proxy carried {carried} B "
                    f"({drift:.1%}, tolerance 10%)", drift <= 0.10)
        ok &= _mc(f"{driver}: past the 0.15 MB budget a request the page "
                    f"makes never reaches the proxy, and check() stops the "
                    f"run before its next navigation (proxy saw {stub.paths})",
                    isinstance(stopped, proxy_meter.ProxyBudgetExceeded)
                    and "/a.js" in stub.paths and "/n" not in stub.paths)
    if not ran:
        print("  no browser driver installed")
    return ok


def test_canary_reports_proxy_traffic():
    _mgroup("Canary: proxy traffic in the step summary, budget enforced")
    import subprocess
    ok = True
    path = os.path.join(REPO_ROOT, ".github", "workflows", "canary.yml")
    if not os.path.exists(path):
        # The image build copies the code and the suite, not .github.
        print("  not run — no .github/workflows in this tree")
        return True
    text = open(path, encoding="utf-8").read()
    steps = re.split(r"\n\s*- (?:name|uses):", text)
    runs = [s for s in steps if re.search(r"\w+_scraper\.py", s)
            and "--out" in s]
    ok &= _mc(f"every canary scrape passes --proxy-budget-mb 10 "
                f"({len(runs)} run step(s))", bool(runs) and all(
                    "--proxy-budget-mb 10" in r for r in runs))
    outs = set()
    for r in runs:
        outs.update(re.findall(r"--out\s+(\S+)", r))
    reporters = [s for s in steps if ".github/proxy_traffic.py" in s
                 and "!cancelled()" in s]
    reported = set()
    for s in reporters:
        reported.update(re.findall(r"proxy_traffic\.py\s+\S+\s+(\S+)", s))
    ok &= _mc(f"each run's proxy traffic goes to the step summary, even "
                f"after a failed step (runs {sorted(outs)}, reported "
                f"{sorted(reported)})", bool(outs) and outs <= reported)

    script = os.path.join(REPO_ROOT, ".github", "proxy_traffic.py")
    with tempfile.TemporaryDirectory() as tmp:
        summary = os.path.join(tmp, "summary.md")
        results = {}
        for case, meta in (
                ("over", {"proxy_bytes": 12_300_000,
                          "proxy_budget_bytes": 10_000_000,
                          "proxy_budget_exceeded": True}),
                ("under", {"proxy_bytes": 2_340_000,
                           "proxy_budget_bytes": 10_000_000,
                           "proxy_budget_exceeded": False}),
                ("plain", {"status": "complete"}),
                ("missing", None)):
            out = os.path.join(tmp, case)
            if meta is not None:
                with open(out + ".latest_attempt.meta.json", "w") as f:
                    json.dump(meta, f)
            proc = subprocess.run(
                [sys.executable, script, "live/" + case, out],
                capture_output=True, text=True,
                env={**os.environ, "GITHUB_STEP_SUMMARY": summary})
            results[case] = (proc.returncode, proc.stdout)
        written = open(summary).read() if os.path.exists(summary) else ""
    rc, out = results["over"]
    ok &= _mc(f"over budget: '::error::stopped: proxy budget 10 MB "
                f"exceeded' and the step fails (rc={rc})", rc == 1
                and "::error::stopped: proxy budget 10 MB exceeded" in out)
    ok &= _mc("under budget: '**live/under**: proxy traffic 2.3 MB of "
                "10 MB' in the summary, step passes",
                results["under"][0] == 0
                and "**live/under**: proxy traffic 2.3 MB of 10 MB" in written
                and "**live/over**: proxy traffic 12.3 MB of 10 MB" in written)
    ok &= _mc("no proxy fields / no sidecar: reported, not a crash",
                results["plain"][0] == 0 and results["missing"][0] == 0
                and "not metered" in written and "unknown" in written)
    return ok



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

