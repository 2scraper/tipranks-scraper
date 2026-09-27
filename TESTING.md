# Testing

## Two different claims, kept apart

`python3 smoke_test.py` proves the parsing/schema/exit-code logic is
correct against real captured data, without a browser. It does **not**
prove that a real browser session against the live site actually reaches
that logic — that's what a live run (further down) is for. Treat a green
`smoke_test.py` as "the code is correct given this input," not "this
works against tipranks.com right now."

## What `smoke_test.py` covers

- `tipranks_parser.find_ticker_record` picks the requested ticker out of
  a payload's `models.stocks` list, never `stocks[0]` (that list also
  carries unrelated sidebar entries).
- `parse_payload` extracts every confirmed field correctly from a real
  captured fixture (`tests/fixtures/tipranks_aapl_stock_analysis.json`),
  and degrades gracefully — never crashes, never fabricates a value —
  when a real payload has a price target but no consensus breakdown
  (`tipranks_luv_sparse_consensus.json`), or no `analystRatings` at all
  (`tipranks_gme_no_ratings.json`).
- `humanize_consensus_id` formats every consensus id actually observed
  live (`moderateBuy`, `strongBuy`, `neutral`, `moderateSell`).
- `is_allowed()` re-derives its robots.txt answers from the captured text
  itself (longest-match-wins), not a hardcoded conclusion.
- `output_writer.finish_run`'s outcome precedence
  (`remote_api_error > blocked > empty > partial > complete`), and that a
  zero-result run without `--allow-empty` writes neither the output file
  nor its `.meta.json` sidecar.
- All three engines actually reach `EXIT_REMOTE_API_ERROR` (5) when every
  requested ticker fails and none complete, and `EXIT_BLOCKED` (3) when
  every ticker was specifically blocked (a real 403/429) — both by
  injecting a fake `run()` per engine, not just trusting the exit codes
  are named correctly. Found live: this used to be broken — all three
  engines passed `remote_api_error=False`/`blocked=False`
  unconditionally, so a total network outage was indistinguishable from
  the site legitimately returning nothing (both landed on
  `EXIT_ZERO_PRODUCTS`), and a real block could never surface as
  anything but a generic failure. Fixed once in
  `output_writer.finish_run()` (auto-derives `remote_api_error` from
  `tickers_completed`/`failed_tickers`, the same way it already derived
  `partial`) plus a real 403/429 check in each engine's own fetch
  function. A genuine partial run (some tickers succeed, others fail)
  is separately checked to confirm it still reaches `EXIT_PARTIAL` and
  isn't swallowed by either fix above.
- `write_json`/`write_csv` round-trip the full `TickerRating` schema.
- `diff_runs.py` against two real `finish_run()` outputs correctly
  reports added/removed/rating-changed/price-target-changed/smart-score-changed.
- All three engines (`playwright_scraper.py`, `selenium_scraper.py`,
  `puppeteer_scraper.py`) import cleanly with **no** engine driver
  installed, and reject bad usage (no tickers, a robots.txt-disallowed
  path, an unreadable `--tickers-file`) identically, before any network
  call.
- The credential scanner (`.github/ci_checks.py`) passes, and the
  Dockerfile (when present) strips the test suite and never `COPY`s a
  `.env` — both guarded to skip cleanly in contexts (like a Docker
  build's own stripped-down `COPY` list) that don't have every file.

## What it does not cover

There is no `local_e2e_test.py` in this repo (a sibling scraper with a
proxy pool and multi-page pagination has one, to reproduce a
control-flow bug class that genuinely doesn't exist here — a single
ticker fetch has no rotation/pagination state to get wrong). The engine
scripts' actual browser-automation code (`page.goto` + the in-page
`fetch()` of `payload.json`, per engine) is only proven by a real live
run, below — `smoke_test.py` never launches a browser at all.

The `EXIT_BLOCKED` path has now fired in a real Playwright run during a
pre-release audit, after an earlier run from the same environment completed
successfully. `smoke_test.py` still proves the wiring deterministically by
injecting a fake blocked result; the live condition cannot be provoked on
demand and belongs to the scheduled canary.

## Live testing checklist

1. `pip install -r requirements-playwright.txt && playwright install chromium`
2. `python3 playwright_scraper.py --ticker AAPL --format json --out out.json` — inspect `out.json` and `out.json.meta.json` by hand. This is the single most important run: everything else here is a variation on it.
3. Repeat with `--ticker ZZZZINVALID` (or any ticker you know doesn't exist) — confirm it's recorded as a completed ticker contributing zero rows, not a failure.
4. Repeat with several real tickers at once, including one with a `.` in the symbol (e.g. `BRK.B`, confirmed live — see `tipranks_parser.py`).
5. Selenium: `pip install -r requirements-selenium.txt`, run the same commands against `selenium_scraper.py`. Confirm `driver.title`-based not-found detection actually fires on a real invalid ticker (see that file's module docstring for why it differs from the other two engines).
6. Puppeteer: `pip install -r requirements-puppeteer.txt`, same commands against `puppeteer_scraper.py`.
7. `python3 diff_runs.py out1.json out2.json` against two real runs a few minutes apart — confirm `price target changed`/`smart score changed` behave sensibly (small moves are normal; see `tipranks_parser.py`'s note on the payload's own edge-cache TTL for why a very recent price can lag).
8. Grep the actual stdout/output files for anything unexpected — this repo has no credentials to leak, but confirm a crash's traceback doesn't contain anything it shouldn't either.
9. `docker build -t tipranks-scraper .` then `docker run --rm tipranks-scraper --ticker AAPL` — confirm the entrypoint works and the image doesn't ship `tests/`/`smoke_test.py`.
10. CI: this repo's `canary` workflow needs no secret to run. It fails on a
    block, remote error, partial run, or zero ratings; a green badge therefore
    means the GitHub runner collected real data. It also checks that the
    captured robots.txt Allow/Disallow rules still match the live file.

## What "done" looks like

`smoke_test.py` green with no engine installed, green again in each engine's
own virtualenv per CI's `engine-smoke` matrix, manually recorded live runs
against at least one real ticker per engine (step 5/6 above), and a Docker
image that builds and runs. Pull-request CI enforces the offline, import and
Docker portions; the scheduled canary live-tests Playwright only. Selenium
and Puppeteer live checks remain an explicit release checklist item.
