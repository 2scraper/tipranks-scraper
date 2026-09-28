# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/) as closely as a CLI toolkit can manage.

## [Unreleased]

### Fixed — 2026-09-28, `canary.yml` failing on a real (expected) datacentre-IP block

- Canary run #1 — its first fire ever, scheduled — hit a real block:
  TipRanks refused the scrape from GitHub Actions' own datacentre IP for
  all three test tickers (`exit=3, status=blocked`). The job failed
  (red badge), which surprised Roman since `CONTRIBUTING.md` already
  promised the opposite: "a live-site block from a datacentre IP is a
  reported warning there, not a merge-blocking failure" — a promise the
  workflow itself never actually implemented.
- Fixed `canary.yml` to match what `CONTRIBUTING.md` already said: a
  crash or bad-usage exit (1/2) — a real bug in this repo — still fails
  the job. A block (exit 3) now prints a `::warning::` annotation and
  does NOT fail the job; `canary_out.json.meta.json` is still uploaded
  every run, so the actual outcome stays visible even on a green run.
  Zero-products/remote-api-error/partial (4/5/6) still fail the job —
  unlike a block, those can also mean this repo's own parser or wiring
  broke, not just an external condition, so they stay load-bearing.
- This changes what the canary badge means: green now means "ran without
  crashing", not "definitely collected real data" — `README.md` and
  `TESTING.md` updated to say so plainly, resolving the contradiction
  with `CONTRIBUTING.md` rather than picking a side silently.
- No proxy/anti-detection was added — this only changes whether a real,
  external, out-of-this-repo's-control block reaches the badge. Whether
  this repo should get a proxy so its own canary can see past this class
  of block is a separate, not-yet-decided question.

## [0.3.0] - 2026-09-28

### Added

- **The full 2Captcha toolkit, ported from shein-scraper/g2-scraper**:
  `--proxy`/`--proxy-file`/`--proxy-shuffle`, `--cdp-endpoint`,
  `--fingerprint`/`--fp-tags`/`--fp-country`, `--twocaptcha-key`/
  `--captcha-api`/`--solve-captcha`/`--min-score`, and `--scraper-api`/
  `--scraper-api-timeout`/`--scraper-api-url`/`--scraper-api-cdp`/
  `--scraper-api-country`/`--scraper-api-profile-id`, identical across
  all three engines (CLAUDE.md §4). New shared modules `env_config.py`,
  `proxy_pool.py`, `captcha_solver.py`, `fingerprint_client.py`,
  `scraper_api_client.py`, and a new `.env.example`.
  **Why, stated plainly**: unlike every other family member, this repo
  shipped 0.1.0-0.2.1 with zero 2Captcha products — the ticker forecast
  page is free, public, unauthenticated data, and no block had ever been
  observed. A pre-release audit (see 0.2.0's own canary-tightening entry)
  then hit a real one: an HTTP 403 Cloudflare "Just a moment..." JS
  challenge, carrying a `__cf_chl_rt_tk` URL token, fired after an
  earlier successful run in the same session (a behavior/volume-
  triggered challenge, not a flat rate limit). `tipranks_parser.py` now
  exposes this as two confirmed markers, `BLOCK_BODY_MARKERS`/
  `BLOCK_URL_MARKERS`, checked by every engine's fetch function and its
  `--scraper-api` path.
  **What this addition is NOT**: a confirmed CAPTCHA fix. No Cloudflare
  Turnstile, hCaptcha, or other known solvable widget script has ever
  been found in any capture from this site — see `captcha_solver.py`'s
  module docstring for the honesty caveat this carries. `--proxy` and
  `--cdp-endpoint` (a fresh exit IP / a managed device identity) are
  this repo's own evidence-backed mitigations; the captcha-detection/
  solving plumbing is family-standard code that may simply find nothing
  to solve here, and is shipped anyway for parity and in case that
  changes.
  **`--scraper-api`'s own divergence from shein-scraper/g2-scraper**:
  those siblings point the Scraper API at the rendered page and parse
  embedded state out of the HTML it returns. This repo's parser instead
  needs the same-origin `stock-analysis/payload.json` JSON endpoint,
  normally fetched via an in-page, cookie-carrying `fetch()` call — the
  Scraper API can't run arbitrary page JS and hand back the result, so
  `--scraper-api` here fetches `payload.json` directly instead. Whether
  that endpoint answers a fresh, cookie-less request the same way it
  answers the in-page authenticated fetch is a genuinely untested
  assumption, stated plainly in `_fetch_one_via_scraper_api()`'s own
  docstring — confirm live before relying on it (TESTING.md).
  **Selenium's own standing CLAUDE.md §6 limits apply here too**:
  `--cdp-endpoint` is refused outright (`EXIT_BAD_USAGE`) when it carries
  credentials (chromedriver cannot authenticate a remote CDP session);
  `--proxy` credentials are stripped before reaching Chrome's
  `--proxy-server`, with a warning, not silently dropped.
  **Scoping decision, stated plainly**: unlike shein-scraper/g2-scraper
  (which rotate to a fresh proxy per scroll round), this repo picks ONE
  proxy for the whole run and does not rotate mid-run — a per-ticker JSON
  fetch loop doesn't carry the volume that rotation was built for, and
  the added complexity has no evidence yet that it's needed here.
- `requests` is now a real dependency (`requirements.txt`) — 2Captcha's
  API and the Scraper API both need it. `python-dotenv` stays optional
  (`env_config.py` falls back to a small hand-rolled `.env` parser).
- 18 new `smoke_test.py` checks (30 → 48): `env_config.ENV_KEYS` against
  `.env.example` in both directions, placeholder/precedence behavior,
  proxy parsing/masking/redaction, `scraper_api_client` key/override
  handling, `--scraper-api-cdp` requiring `--scraper-api` (structural and
  behavioral, via a faked `TwoCaptchaClient.scrape_url` across all three
  engines), confirmation that `--scraper-api` fetches `payload.json` (not
  the rendered page), a real-fixture round-trip through `--scraper-api`
  to `EXIT_OK`, a Cloudflare-marker body correctly reaching
  `EXIT_BLOCKED` via `--scraper-api`, `_maybe_solve_captcha`'s no-op
  contract, `--block-retries`, Selenium's credentialed-`--cdp-endpoint`
  refusal, and the block-marker helpers themselves.
- Credential scanner (`.github/ci_checks.py`) upgraded from "no real
  credential model" to the same one shein-scraper/g2-scraper use,
  including their two already-fixed false-positive classes (an
  unanchored `openai_key` pattern; a Python type-hint token like
  `api_key: Optional[str]` colliding with the unquoted secret-assignment
  pattern) ported in rather than rediscovered here.

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

[Unreleased]: https://github.com/2scraper/tipranks-scraper/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/2scraper/tipranks-scraper/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/2scraper/tipranks-scraper/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/2scraper/tipranks-scraper/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/2scraper/tipranks-scraper/releases/tag/v0.1.0
