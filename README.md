# TipRanks Scraper

![release](https://img.shields.io/github/v/release/2scraper/tipranks-scraper)
![tests](https://github.com/2scraper/tipranks-scraper/actions/workflows/tests.yml/badge.svg)
![canary](https://github.com/2scraper/tipranks-scraper/actions/workflows/canary.yml/badge.svg)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
![licence](https://img.shields.io/badge/licence-MIT-green)
![engines](https://img.shields.io/badge/engines-Playwright%20%7C%20Selenium%20%7C%20Puppeteer-informational)
![credentials-optional](https://img.shields.io/badge/credentials-optional-blue)

Scrapes one thing from tipranks.com: a stock ticker's forecast-page
snapshot — Smart Score, analyst consensus (Buy/Hold/Sell), and price
target — from the site's free, unauthenticated data. No account, no
login required for a normal run — this repo is **local-first**: the
default is a plain local headless Chromium against a free, public page,
no proxy, no captcha-solving key. `--proxy`/`--cdp-endpoint`/
`--fingerprint`/`--twocaptcha-key`/`--scraper-api` (added 2026-09-28,
after a real Cloudflare block was observed — see "What's actually in
front of the site" below) are opt-in mitigations, not a requirement.
Three engines (Playwright, Selenium, Puppeteer/pyppeteer) with an
identical CLI, output schema and exit codes. JSON or CSV output.

## What it reads

`https://www.tipranks.com/stocks/{ticker}/stock-analysis` is a
client-rendered page that loads its own data from a same-origin JSON
endpoint, `.../stock-analysis/payload.json`. This scraper drives a real
browser to that page and reads that same JSON — no CSS-selector/DOM
scraping for any field below.

Per ticker:

- `smart_score`, `smart_score_updated` — TipRanks' own 1-10 score.
- `consensus_rating_id` / `consensus_rating_label` — the site's own
  Buy/Hold/Sell consensus label (its raw id, e.g. `"moderateBuy"`, and a
  human-formatted version, `"Moderate Buy"`).
- `analyst_count`, `buy_count`, `hold_count`, `sell_count`.
- `price_target_average`, `price_target_high`, `price_target_low`,
  `price_target_upside` (a fraction, e.g. `-0.018` = -1.8%).
- The same set again for TipRanks' "Best Performing Analysts" subset,
  prefixed `best_*` — a different, smaller population than the fields
  above, never merged with them.
- `current_price` and `current_price_asof` — best-effort. The payload's
  quote can lag the page's own rendered price; treat this as a
  possibly-stale snapshot, not a real-time quote.
- `company_name`, `currency`, `source_url`, `scraped_at`.

Any of the consensus/count/price-target fields can be `None` — some
tickers carry a price target with no buy/hold/sell breakdown at all, and
some (confirmed live: GME) carry no analyst data at all — a real,
observed shape, not a parser bug. See `tipranks_parser.py`'s module
docstring for exactly what's confirmed from a live capture versus what's
explicitly out of scope.

## Install and run

Pick one engine — all three produce identical output:

```bash
# Playwright (primary)
pip install -r requirements-playwright.txt
playwright install chromium
python3 playwright_scraper.py --ticker AAPL --ticker MSFT --format json --out results.json

# Selenium
pip install -r requirements-selenium.txt
python3 selenium_scraper.py --ticker AAPL --format json --out results.json

# Puppeteer (pyppeteer)
pip install -r requirements-puppeteer.txt
python3 puppeteer_scraper.py --ticker AAPL --format json --out results.json
```

Install each engine's requirements file in its own virtualenv — their
pins are not all mutually satisfiable in one environment.

Flags, identical across all three:

| Flag | Meaning |
|---|---|
| `--ticker SYM` | A ticker to scrape. Repeatable. |
| `--tickers-file PATH` | One ticker per line, instead of repeating `--ticker`. |
| `--format json\|csv` | Output format. Default `json`. |
| `--out PATH` | Output file path. Default `tipranks_results.json`. |
| `--allow-empty` | Write output even if every requested ticker came back empty. |
| `--headed` | Launch a visible browser instead of headless (debugging). |
| `--executable-path PATH` | Use an already-installed Chrome/Chromium instead of this engine's own bundled-browser download. Normally unnecessary — see Engine notes for the one confirmed case where it isn't. |
| `--block-retries N` | On a blocked outcome, retry that ticker on the SAME browser/session this many extra times before giving up ("retry before you rotate"). Default 2. |

### 2Captcha toolkit — opt-in, not required for a normal run

Added 2026-09-28, after a real Cloudflare block was observed in a
pre-release audit (see "What's actually in front of the site" below).
**Unlike shein-scraper/g2-scraper, this repo has never observed an
actual CAPTCHA widget** — the one real block seen was a Cloudflare
"Just a moment..." JS interstitial (a browser-fingerprinting challenge a
real browser normally clears on its own), not a confirmed Turnstile/
hCaptcha/reCAPTCHA challenge. `captcha_solver.py`'s detection/solving is
family-standard plumbing, shipped for parity and in case that changes —
it may simply find nothing to solve here. `--proxy` (a fresh exit IP)
and `--cdp-endpoint` (a managed device identity) are this repo's own
evidence-backed mitigations for the block actually observed.

`--proxy --proxy-file --proxy-shuffle --cdp-endpoint --fingerprint
--fp-tags --fp-country --twocaptcha-key --captcha-api --solve-captcha
--min-score --scraper-api --scraper-api-timeout --scraper-api-url
--scraper-api-cdp --scraper-api-country --scraper-api-profile-id`

Identical across all three engines (`smoke_test.py` checks the flag sets
and the `--scraper-api-cdp` wiring never drift apart), with two named,
documented exceptions:

- **Selenium cannot authenticate a remote CDP session at all.**
  `--cdp-endpoint` is refused outright (`EXIT_BAD_USAGE`) when it carries
  credentials — the shape of 2Captcha's own Scraping Browser API
  connection string — since chromedriver's `debuggerAddress` takes a
  bare `host:port`. Use `playwright_scraper.py` or `puppeteer_scraper.py`
  for that product. A bare, uncredentialed `--cdp-endpoint` (local
  remote debugging) still works on Selenium.
- **Selenium's `--proxy-server` cannot authenticate at all.** A `--proxy`
  with a login/password has its credentials stripped before reaching
  Chrome, with a logged warning — not silently dropped.

**`--scraper-api`** sends one browserless HTTP call to 2Captcha's Scraper
API (`scraper.2captcha.com`) instead of launching any local or
`--cdp-endpoint` browser. Requires `--twocaptcha-key`/`TWOCAPTCHA_KEY`.
**This repo's own divergence from shein-scraper/g2-scraper**: those
siblings point the Scraper API at the rendered page and parse embedded
state out of the returned HTML. This repo's parser instead needs the
same-origin `stock-analysis/payload.json` JSON endpoint, normally
fetched via an authenticated in-page `fetch()` call — the Scraper API
can't run arbitrary page JS and hand back the result, so `--scraper-api`
here fetches `payload.json` directly. **Whether that endpoint answers a
fresh, cookie-less request the same way it answers the in-page fetch is
a genuinely untested assumption** — see
`playwright_scraper.py`'s `_fetch_one_via_scraper_api()` docstring and
`TESTING.md`. `--headed`/`--executable-path`/`--proxy`/`--cdp-endpoint`/
`--fingerprint` are all ignored in this mode (logged as a warning, not
silently dropped) — a single static fetch per ticker has no browser
session and brings its own exit IP/device.

**`--scraper-api-cdp`** routes `--scraper-api`'s fetch through a 2Captcha
Scraping Browser CDP session (their `cdpurl` field) instead of their own
default pool, chaining two 2Captcha products together — this is what
would give `--scraper-api` real captcha auto-solve and exit-country
pinning, the same way `scraping_browser_connection_url()` already works
for `--cdp-endpoint`. It never touches a caller-supplied
`--cdp-endpoint` — that flag stays ignored in `--scraper-api` mode.
**Wired and covered by `smoke_test.py` (structural + behavioral, with a
faked Scraper API response), but not yet exercised against a real
2Captcha/tipranks.com session** — see `CHANGELOG.md` and `TESTING.md`.

**Scoping decision, stated plainly**: unlike shein-scraper/g2-scraper
(fresh proxy per scroll round), this repo picks ONE proxy for the whole
run and does not rotate mid-run — a per-ticker JSON fetch loop doesn't
carry the volume that rotation was built for.

Credentials belong in `.env` / `TIPRANKS_PROXY` / `TIPRANKS_CDP_ENDPOINT`
/ `TWOCAPTCHA_KEY` — never as literal `--proxy`/`--cdp-endpoint`/
`--twocaptcha-key` text on a shared or logged command line if you can
avoid it. See `.env.example`.

## Output contract

Same exit codes across all three engines:

| Exit | Meaning |
|---|---|
| 0 | complete |
| 1 | crashed |
| 2 | bad usage (no tickers given, robots.txt disallows a path, ...) |
| 3 | blocked (a real 403/429, or the confirmed Cloudflare interstitial's own body/URL markers — see below) |
| 4 | zero results |
| 5 | remote API error (navigation/network failure) |
| 6 | partial (some tickers failed, others succeeded) |

A run where every requested ticker fails and none complete is `5`
(remote API error) unless every one of those failures was specifically a
403/429, in which case it's `3` (blocked) — a total block and a total
network outage are different situations for a caller (site actively
rejected you vs. the network or engine itself broke), and both are
distinct from `4`, where the site was reachable and simply had nothing to
return for a ticker (a confirmed 404/400).

A zero-result run writes neither `--out` nor its `.meta.json` sidecar
unless `--allow-empty` is passed, so it does not overwrite a previous good
result by default. Output and sidecar are replaced atomically and new
sidecars carry an output checksum that `diff_runs.py` verifies. An unknown
ticker (real 404/400 from the site) is
recorded as a *completed* ticker that contributed zero rows, not a
failure — only a network/navigation error counts against the run.

`diff_runs.py old.json new.json` compares two completed runs: added /
removed tickers, and per-ticker rating / price-target / Smart Score
changes.

## robots.txt

`https://www.tipranks.com/robots.txt` disallows `/api/*` and a handful of
other paths, but not `/stocks/*` (aside from `/stocks/*/options-chain/*`,
unrelated to this repo). `tipranks_parser.is_allowed()` re-checks this
from the captured robots.txt text itself at runtime, not from a
hardcoded conclusion.

## What's actually in front of the site

Two edge layers, confirmed from real response headers: **Fastly**
(caching — the HTML page is edge-cached up to 24h, `payload.json` up to
5 minutes) sitting with **Cloudflare** (`server: cloudflare`, `cf-ray`).
The Fastly cache is the confirmed reason a quote can lag the page's own
rendered price (see `current_price`'s caveat above) — it's edge caching,
not a parser bug.

On the challenge side: Cloudflare's bot-management fingerprinting script
loads on every page view, and Google reCAPTCHA's script is also present
on the page (loaded, not auto-rendered). Neither ever actually
challenged a request in this project's research — roughly 20 distinct
tickers and 35+ requests, all clean 200s, no CAPTCHA, no 429. No
DataDome/PerimeterX/Akamai/Imperva/Kasada/Arkose Labs signature was found
either. That's "not observed to challenge this traffic," not "confirmed
unprotected" — there's no known solvable challenge to wire up here
because none was presented in that research session. A later audit did
observe a real one: an HTTP 403 Cloudflare JS challenge ("Just a
moment...", a `__cf_chl_rt_tk` token on the final URL) on the page
navigation, in a local headless Playwright run, after an earlier run from
the same environment had already completed successfully — a behavior-
triggered challenge, not a first-request block. The scraper reports that
as exit 3. **Updated 2026-09-28**: the daily live canary's very first run
hit exactly this — a real block from GitHub Actions' own datacentre IP —
and the canary now reports it as a workflow warning annotation rather
than failing the job (see CONTRIBUTING.md and the workflow's own
comments); a crash or bad-usage exit (a real bug) still fails it. So the
canary's green badge means "the CLI ran without crashing", not "data was
definitely collected" — check the run's uploaded `canary_out.json.meta.json`
artifact for that, or look for a warning annotation on a green run.

## Engine notes

- **Playwright / Puppeteer** read the real HTTP status from the
  navigation response to detect an unknown ticker (a real 404/400), and
  also check the confirmed Cloudflare block markers (`tipranks_parser.
  BLOCK_URL_MARKERS`/`BLOCK_BODY_MARKERS`) against the navigation
  response/URL. Both are also the only two engines that can open an
  authenticated `--cdp-endpoint` session (e.g. the 2Captcha Scraping
  Browser API) and arm its own `Captcha.setAutoSolve` CDP domain.
- **Selenium**: chromedriver doesn't expose the navigation response's
  HTTP status directly, so `selenium_scraper.py` detects a not-found page
  via the confirmed real title string (`"Error 404: Page Not Found"`)
  instead — a documented, named difference, not a silent gap. The same
  status-blindness narrows (but doesn't close) its block detection: it
  checks the same URL/body markers right after navigation, but can only
  see a 403/429 on the in-page `payload.json` fetch, not the initial page
  load. `--cdp-endpoint` is refused outright when it carries credentials
  — see "2Captcha toolkit" above.
- **Puppeteer (legacy/experimental)**: pyppeteer is effectively unmaintained.
  Confirmed live on a real Apple Silicon Mac — pyppeteer's
  own bundled Chromium download (pinned at revision 117.0.5938.0)
  launches but then segfaults on an actual headless run. Playwright's and
  Selenium's own browser/driver downloads were unaffected on the same
  machine, so this is specific to that one pinned build, not this site or
  this repo's code. Fix: `--executable-path /path/to/Chrome` to use an
  already-installed, working Chrome/Chromium instead (all three engines
  accept this flag, for CLI parity, though only Puppeteer has needed it
  so far).

## Testing

`python3 smoke_test.py` — offline, no browser required, runs against
real captured fixtures in `tests/fixtures/`, and against all three
engines with no driver installed. See `TESTING.md` for the full coverage
list and the live-testing checklist.

## Known limitations

- No confirmed CAPTCHA widget on this site — `captcha_solver.py`'s
  solving plumbing is shipped for family parity and may simply find
  nothing to solve; see "2Captcha toolkit" above.
- `--scraper-api` fetches `payload.json` directly instead of the
  rendered page (unlike shein-scraper/g2-scraper); whether that endpoint
  answers a fresh, cookie-less request the way it answers the in-page
  fetch is untested — see "2Captcha toolkit" above.
- `--scraper-api-cdp` is wired and unit-tested but not yet exercised
  against a real 2Captcha/tipranks.com session.
- Only the ticker forecast page. No screener/stock-list pages, no
  insider-trading pages, no hedge fund activity pages — all real
  sections of the same site, out of scope here.
- `enumId` 1 (presumably `"strongSell"`) was never observed live; the
  parser never hardcodes a guess for it — it reads the site's own label
  string directly instead of a private lookup table.
- Untested: non-USD tickers, and a `-` in a ticker symbol (a `.`, e.g.
  `BRK.B`, is confirmed live).
- No `local_e2e_test.py` — see `TESTING.md` for why a single ticker fetch
  doesn't need one the way a paginated/proxy-rotating sibling does.
