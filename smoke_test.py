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
            code = mod.main(["--ticker", "AAPL", "--ticker", "MSFT", "--allow-empty"])
            assert code == ow.EXIT_PARTIAL, f"{mod.__name__}: got {code}"
        finally:
            mod.run = original


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
