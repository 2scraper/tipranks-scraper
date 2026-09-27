# TipRanks Scraper

Scrapes one thing from tipranks.com: a stock ticker's forecast-page
snapshot — Smart Score, analyst consensus (Buy/Hold/Sell), and price
target — from the site's free, unauthenticated data. No account, no
login, no proxy, no captcha solving.

Not yet published anywhere; this is a local, first-pass build.

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
tickers carry a price target with no buy/hold/sell breakdown at all (a
real, observed shape, not a parser bug). See `tipranks_parser.py`'s
module docstring for exactly what's confirmed from a live capture versus
what's explicitly out of scope.

## Install and run

```bash
pip install -r requirements-playwright.txt
playwright install chromium

python3 playwright_scraper.py --ticker AAPL --ticker MSFT --format json --out results.json
```

`--tickers-file path.txt` reads one ticker per line instead of repeating
`--ticker`. `--format csv` writes CSV. `--allow-empty` writes output even
if every requested ticker came back empty (unknown ticker, etc.) instead
of the default behavior described below.

## Output contract

Same exit codes as the rest of the 2scraper family, since a caller
scripting more than one of these benefits from one consistent contract:

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

## robots.txt

`https://www.tipranks.com/robots.txt` disallows `/api/*` and a handful of
other paths, but not `/stocks/*` (aside from `/stocks/*/options-chain/*`,
unrelated to this repo). `tipranks_parser.is_allowed()` re-checks this
from the captured robots.txt text itself at runtime, not from a
hardcoded conclusion.

## Testing

`python3 smoke_test.py` — offline, no browser required, runs against
real captured fixtures in `tests/fixtures/`. No live end-to-end run
against tipranks.com has been done from an automated Playwright session
yet (both sandboxes used to build this could not reach the live site or
download a browser at all); the parser itself is built entirely from
real payloads captured through a live browser session against the
current site, and every fixture is a trimmed, real capture — see
`tipranks_parser.py`'s docstring for the full list of what's confirmed.
Before relying on this, run it once for real:

```bash
python3 playwright_scraper.py --ticker AAPL --format json --out /tmp/aapl.json
cat /tmp/aapl.json
```

## Known limitations

- Only the ticker forecast page. No screener/stock-list pages, no
  insider-trading pages, no hedge fund activity pages — all real
  sections of the same site, out of scope here.
- `enumId` 1 (presumably `"strongSell"`) was never observed live; the
  parser never hardcodes a guess for it — it reads the site's own label
  string directly instead of a private lookup table.
- Untested: non-USD tickers, and tickers with `.`/`-` in the symbol.
- One engine only (Playwright). No Selenium/Puppeteer parity, no CI, no
  Docker image yet — this is a first pass, not a published release.
