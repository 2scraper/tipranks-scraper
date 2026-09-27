# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/) as closely as a CLI toolkit can manage.

## [Unreleased]

## [0.1.0] - 2026-09-27

First tagged release. Three engines (Playwright primary, Selenium and
Puppeteer for parity), one output contract, no credentials of any kind —
the ticker forecast page is free, public data.

### Added

- Three engines for tipranks.com — `playwright_scraper.py` (primary),
  `selenium_scraper.py` and `puppeteer_scraper.py` — scrape a stock
  ticker's forecast-page snapshot from
  `https://www.tipranks.com/stocks/{ticker}/stock-analysis`, with an
  identical CLI flag set, exit codes, and `TickerRating` schema across
  all three. Data comes from the page's own same-origin
  `stock-analysis/payload.json` endpoint (a real, confirmed JSON API),
  never DOM/CSS scraping.
- `TickerRating` schema: Smart Score, the "all analysts" consensus
  (Buy/Hold/Sell counts, consensus label, price target average/high/low/
  upside), the "best performing analysts" subset of the same fields, and
  a best-effort current price. Every field is independently optional —
  some real tickers carry a price target with no consensus breakdown, or
  no analyst data at all.
- robots.txt honoured: `tipranks_parser.is_allowed()` re-derives its
  answer from a captured copy of the real robots.txt text at runtime
  (longest-match-wins), not a hardcoded conclusion.
- JSON and CSV output with a documented schema, a `.meta.json` sidecar on
  every completed/partial run, and the family exit-code contract (`0`
  complete, `1` crash, `2` bad usage, `3` blocked, `4` zero results, `5`
  remote API error, `6` partial). A failed or empty run writes neither
  file, so it can never overwrite a previous good result.
- `diff_runs.py` for comparing two completed runs (added / removed /
  rating changed / price target changed / smart score changed).
- Offline test suite (`smoke_test.py`, no engine driver required). CI
  runs it on two Python versions plus one `engine-smoke` job per engine,
  builds and exercises the Docker image, and ships a daily `canary`
  workflow against the live site — needing no secret to run, since this
  site's data is free and public.
- Docs: `README.md`, `TESTING.md`, `CONTRIBUTING.md`, `SECURITY.md`,
  `CODE_OF_CONDUCT.md`, `landing.md`/`landing.html`, and
  `sample_output.json`/`sample_output.csv` (real captured AAPL data).

### Known limitations

- One-page scope: only the ticker forecast page. No screener/stock-list
  pages, no insider-trading pages, no hedge fund activity pages — all
  real sections of the same site, deliberately out of scope.
- `enumId` 1 (presumably `"strongSell"`) was never observed live in this
  project's research; the parser reads the site's own label string
  directly rather than hardcoding a table that would have to guess it.
- No `local_e2e_test.py` — a single ticker fetch has no
  pagination/proxy-rotation control-flow state to reproduce a bug class
  against (see `TESTING.md`).
- Untested: non-USD tickers, and a `-` in a ticker symbol (a `.`, e.g.
  `BRK.B`, is confirmed live).

[Unreleased]: https://github.com/2scraper/tipranks-scraper/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/2scraper/tipranks-scraper/releases/tag/v0.1.0
