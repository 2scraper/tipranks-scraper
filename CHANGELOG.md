# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/) as closely as a CLI toolkit can manage.

## [Unreleased]

## [0.2.1] - 2026-09-27

### Fixed

- **Docker image was broken**: this was the first time `docker build`
  had ever actually been run (previously only checked statically by
  `smoke_test.py`), and CI's own `docker` job failed immediately. Two
  causes, both in the Dockerfile's `COPY` list: `diff_runs.py` had never
  been copied into the image at all, so its own two tests failed with
  `ModuleNotFoundError` inside the build; separately, a new smoke-test
  check (added in 0.2.0) read `pyproject.toml`/`CHANGELOG.md` to cross-
  check the version against the changelog, but neither file is ever
  `COPY`'d into the image (deliberately — they're contributor metadata,
  not something the shipped image needs), so it failed with a bare
  `FileNotFoundError` instead of skipping. `diff_runs.py` is now in the
  `COPY` list (it's shipped, family-shared functionality per CLAUDE.md
  Sec.7, not test-only); the version/changelog check now skips cleanly
  when either file is absent, the same way the credential-scanner check
  already did.

## [0.2.0] - 2026-09-27

### Added

- `--executable-path` flag on all three engines, to launch an
  already-installed Chrome/Chromium instead of an engine's own
  bundled-browser download.
- A real 403/429 check in each engine's fetch function, classified as
  `"blocked"` — previously indistinguishable from any other remote
  error.

### Fixed

- **Exit-code contract bug**: all three engines were calling
  `finish_run(blocked=False, remote_api_error=False)` unconditionally,
  regardless of what actually happened. This made `EXIT_BLOCKED` (3) and
  `EXIT_REMOTE_API_ERROR` (5) both dead code paths — named in the
  contract but never reachable — and meant a total network outage (every
  ticker fails, none complete) was silently reported as `EXIT_ZERO_
  PRODUCTS` (4), identical to the site legitimately returning nothing.
  `output_writer.finish_run()` now auto-derives `remote_api_error` from
  `tickers_completed`/`failed_tickers` (the same way it already derived
  `partial`), suppressed when the caller signals `blocked=True` instead,
  so a total block correctly surfaces as `3` rather than `5`. All three
  engines now pass a real `blocked` flag, computed from the new 403/429
  check above. `smoke_test.py` gained three regression checks: total
  failure → `5`, all-blocked → `3`, and a genuine partial run is
  confirmed to still reach `6` and isn't swallowed by either fix.
- Puppeteer engine: confirmed live on a real Apple Silicon Mac that
  pyppeteer's own bundled Chromium (pinned revision 117.0.5938.0)
  segfaults on an actual headless run, even though it answers
  `--version` fine — Playwright's and Selenium's own downloads were
  unaffected on the same machine. `--executable-path` pointed at a
  working system Chrome is the fix; documented in
  `puppeteer_scraper.py`'s module docstring.
- **Playwright engine caught a real navigation failure as a crash**:
  `fetch_one()` only caught `PlaywrightTimeoutError` around `page.goto()`,
  so any other navigation failure (DNS, connection refused, a broken
  proxy/tunnel) propagated up as `EXIT_CRASH` (1) instead of `EXIT_
  REMOTE_API_ERROR` (5), violating CLAUDE.md §10. Selenium and Puppeteer
  already caught navigation failures broadly; Playwright now matches them.
- Reject unsafe ticker path/query input before URL construction
  (`tipranks_parser.normalize_ticker()`); trim and canonicalize valid
  ticker symbols consistently across all engines.
- Malformed or unexpected payload shapes (a non-dict `analystRatings`,
  a `quotes` list instead of an object, a schema-drifted record) now
  degrade a single ticker to `remote_api_error` instead of crashing the
  whole run; `find_ticker_record`/`_extract_scope`/`parse_payload` type-
  check every optional structure before reading it.
- Atomically replace output and sidecar files (write to a temp file,
  `fsync`, `os.replace`), record an output SHA-256 in every new sidecar,
  and have `diff_runs.py` refuse an output/sidecar pair whose checksum
  doesn't match (a sign the previous write was interrupted).
- Credential scanner (`.github/ci_checks.py`): `SECRET_ASSIGNMENT` only
  matched a quoted value: `TOKEN="..."`. An unquoted assignment (as it'd
  appear in a shell env or a YAML `env:` block) went undetected. Now
  matches both forms.
- Live canary (`.github/workflows/canary.yml`): now fails the job on any
  non-`complete` outcome (blocked, remote error, partial, zero ratings)
  instead of only warning, so a green badge means real data was actually
  collected, not just that the CLI didn't crash. It also fetches
  `https://www.tipranks.com/robots.txt` at run time and fails if the
  parsed Allow/Disallow rules no longer match the captured copy
  (`tipranks_parser._robots_rules` is now the public
  `parse_robots_rules(text)` so the canary can reuse it against live
  text). **This tightening follows a real block observed in a
  pre-release audit: an HTTP 403 Cloudflare JS challenge ("Just a
  moment...") on the page navigation, in a local headless Playwright
  run, after an earlier run from the same environment had already
  completed successfully — see `tipranks_parser.py`'s module
  docstring.**
- `smoke_test.py`: stopped writing generated output into the repo's own
  working tree (temp dirs instead); added coverage for ticker
  normalization, malformed-payload tolerance, the checksum/sidecar
  check, a mixed blocked+network-error precedence case, the Playwright
  navigation-error fix above, and both credential-assignment forms.

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

[Unreleased]: https://github.com/2scraper/tipranks-scraper/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/2scraper/tipranks-scraper/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/2scraper/tipranks-scraper/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/2scraper/tipranks-scraper/releases/tag/v0.1.0
