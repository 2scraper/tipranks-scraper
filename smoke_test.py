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

