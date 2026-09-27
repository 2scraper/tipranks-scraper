# TipRanks Scraper

![tests](https://github.com/2scraper/tipranks-scraper/actions/workflows/tests.yml/badge.svg)
![canary](https://github.com/2scraper/tipranks-scraper/actions/workflows/canary.yml/badge.svg)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
![licence](https://img.shields.io/badge/licence-MIT-green)
![engines](https://img.shields.io/badge/engines-Playwright%20%7C%20Selenium%20%7C%20Puppeteer-informational)
![no-credentials](https://img.shields.io/badge/credentials-none%20needed-success)

Scrapes one thing from tipranks.com: a stock ticker's forecast-page
snapshot — Smart Score, analyst consensus (Buy/Hold/Sell), and price
target — from the site's free, unauthenticated data. No account, no
login, no proxy, no captcha solving. Three engines (Playwright, Selenium,
Puppeteer/pyppeteer) with an identical CLI, output schema and exit codes.
JSON or CSV output.

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

## Output contract

Same exit codes across all three engines:

| Exit | Meaning |
|---|---|
| 0 | complete |
| 1 | crashed |
| 2 | bad usage (no tickers given, robots.txt disallows a path, ...) |
| 3 | blocked |
| 4 | zero results |
| 5 | remote API error (navigation/network failure) |
| 6 | partial (some tickers failed, others succeeded) |

A zero-result run writes neither `--out` nor its `.meta.json` sidecar
unless `--allow-empty` is passed, so a failed run never overwrites a
previous good result. An unknown ticker (real 404/400 from the site) is
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
because none was ever presented. See `.github/workflows/canary.yml` for
how the daily live check treats a possible block from a datacentre IP.

## Engine notes

- **Playwright / Puppeteer** read the real HTTP status from the
  navigation response to detect an unknown ticker (a real 404/400).
- **Selenium**: chromedriver doesn't expose that status directly, so
  `selenium_scraper.py` detects a not-found page via the confirmed real
  title string (`"Error 404: Page Not Found"`) instead — a documented,
  named difference, not a silent gap.
- **Puppeteer**: confirmed live on a real Apple Silicon Mac — pyppeteer's
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
