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
- `current_price` and `current_price_asof` — the most recent price the
  payload carries: usually the last trading day's close (`current_price_source`
  `daily` or `daily_history`), the same price the site computes
  `price_target_upside` from. The payload's own `quotes` block is used only
  when it is newer; on 2026-10-19 it was weeks or months old where present.
  Not a real-time quote.
- `company_name`, `currency`, `source_url`, `scraped_at`.

From the same payload, added in 0.6.0 as columns after the ones above
(so existing CSV column positions are unchanged):

- **Dividends**: `dividend_amount`, `dividend_ex_date`,
  `dividend_pay_date`, `dividend_frequency`,
  `dividend_annualized_payout`, `dividend_yield`, `dividend_payout_ratio`
  for the latest dividend, and `next_dividend_amount` / `_ex_date` /
  `_pay_date` once the next one is declared. All empty for a non-payer.
  A suspended payer keeps its last dividend with an empty yield (GME:
  2019), so check `dividend_ex_date`.
- **Earnings**: `last_earnings_date`, `last_earnings_eps`,
  `last_earnings_eps_estimate`, `next_earnings_date`,
  `next_earnings_time` (`PreMarket` / `AfterHours` / `Unknown`),
  `next_earnings_eps_estimate`, `next_earnings_revenue_estimate`.
- **Insiders**: `insider_sentiment`, `insider_signal` (0–1),
  `insider_net_usd` (+ buying, − selling). Sentiment and signal are empty
  and the value 0 when the site has nothing to report.
- **Hedge funds**: `hedge_fund_sentiment`, `hedge_fund_signal`,
  `hedge_fund_net_shares` (net change in shares held).
- **TipRanks retail investors**: `investor_sentiment`, `investor_signal`,
  `investor_change_7d`, `investor_change_30d` (change in the share of
  portfolios holding it).
- **News**: `news_sentiment`, `news_sentiment_score`,
  `news_bullish_ratio`, `news_articles_last_week`,
  `news_articles_weekly_average`.
- **Bloggers**: `blogger_sentiment`, `blogger_bullish_ratio`,
  `blogger_opinion_count`.

Sentiment values are the site's own ids: `veryNegative`, `negative`,
`neutral`, `positive`, `veryPositive`. Fractions are fractions (0.025 =
2.5%), dates are the site's ISO strings. The payload does not state the
period behind the insider and hedge fund figures; they are the numbers
the site's own cards show. On the S&P 500 (2026-10-20): dividends 414 of
503, earnings dates 502, hedge funds 502, insiders 268, news sentiment
491, bloggers 503.

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
| `--tickers-file PATH` | One ticker per line, instead of repeating `--ticker`. Blank lines and `#` comments (whole-line or after a ticker) are skipped, as in `--proxy-file`. |
| `--format json\|csv` | Output format. Default `json`. |
| `--out PATH` | Output file path. Default `tipranks_results.json`. |
| `--allow-empty` | Write output even if every requested ticker came back empty. |
| `--headed` | Launch a visible browser instead of headless (debugging). |
| `--executable-path PATH` | Use an already-installed Chrome/Chromium instead of this engine's own bundled-browser download. Normally unnecessary — see Engine notes for the one confirmed case where it isn't. |
| `--block-retries N` | On a blocked outcome, retry that ticker on the SAME browser/session this many extra times before giving up ("retry before you rotate"). Default 2; `0` means one fetch and no retry; a negative value is rejected (exit 2). At most one paid captcha solve is bought per ticker, however many retries, and only when a retry follows to use it. |

### 2Captcha toolkit — opt-in, not required for a normal run

Added 2026-09-28, after a real Cloudflare block was observed in a
pre-release audit (see "What's actually in front of the site" below).
**This repo has never observed an actual CAPTCHA widget** — the one real block seen was a Cloudflare
"Just a moment..." JS interstitial (a browser-fingerprinting challenge a
real browser normally clears on its own), not a confirmed captcha-widget
challenge. `captcha_solver.py`'s detection/solving is
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
**It fetches the JSON endpoint, not the rendered page**, like every
browser engine (see "Engine notes"). Verified live on 2026-10-19 — see
`TESTING.md`. `--headed`/`--executable-path`/`--proxy`/`--cdp-endpoint`/
`--fingerprint` are all ignored in this mode (logged as a warning, not
silently dropped) — a single static fetch per ticker has no browser
session and brings its own exit IP/device.

**`--scraper-api-cdp`** routes `--scraper-api`'s fetch through your own
2Captcha Scraping Browser session (their `cdpurl` field) instead of their
default pool. It needs that session's endpoint in `--cdp-endpoint` /
`TIPRANKS_CDP_ENDPOINT`: the Scraping Browser has its own login and
password, separate from the API key. `--scraper-api-country` and
`--scraper-api-profile-id` replace the endpoint's `-country-`/`-pid-`
parts. Verified live on 2026-10-19 (AAPL and MSFT, complete).

**Proxies**: a run uses ONE proxy and does not rotate per ticker or on
a block, since a per-ticker JSON fetch loop doesn't carry the volume that
proxy rotation is built for. The exception is a dead proxy, one the
browser cannot get through at all (`ERR_PROXY_CONNECTION_FAILED` and the
like). Then the browser is relaunched on the next proxy in
`--proxy-file`, the same ticker is fetched again, and a warning names
both proxies (passwords masked). A dead proxy with nothing left to switch
to fails its tickers as remote errors (exit 5) with a warning saying so,
and the last proxy is never dropped, so the run keeps trying it. Any
other fetch error is printed as `Could not fetch TICKER: <reason>`.
Verified live on 2026-10-20 on all three engines: two dead proxies then a
working one gave exit 0, including a switch to a password-protected
proxy on Playwright and pyppeteer.

**Proxy traffic savers.** A proxy is billed by traffic, so a run through
`--proxy` / `--proxy-file` / `TIPRANKS_PROXY` does two things to avoid
paying for bytes it does not use:

- **One pre-check before any browser starts**: a CONNECT through the
  proxy and a `HEAD /robots.txt` on www.tipranks.com, on the proxy the run
  is about to use. Chromium reports a proxy's HTTP 407 as a navigation
  timeout, so a wrong or exhausted credential used to look like a site
  failure. Now a **407** (the gateway refused the credential, or the
  traffic balance is exhausted) or a **402** (proxy traffic/balance
  exhausted) ends the run in seconds with **exit 5** and
  `<out>.latest_attempt.meta.json` saying `status: failed`,
  `stop_reason: proxy_auth_failed` (or `proxy_balance_exhausted`). Any
  previous output and its `.meta.json` stay as they were. The message
  names `TIPRANKS_PROXY` and prints neither the login nor the password. A
  proxy that does not answer at all keeps the behaviour above (the browser
  makes its own attempt). No pre-check runs without a proxy or with
  `--cdp-endpoint`, which uses its own exit instead of the proxy.
- **Images, video/audio and web fonts are not downloaded** through a local
  proxy: Playwright aborts them by resource type, pyppeteer through CDP's
  Fetch domain, Selenium by file extension with CDP
  `Network.setBlockedURLs`. The data comes from `payload.json` alone. Measured
  2026-10-20 (AAPL and MSFT, no proxy, blocking forced on vs off): the
  same 2 rows and identical values in every column except `scraped_at`.
  Since the engines open `payload.json` directly, a normal run requests
  almost no media in the first place, and this mostly guards against a
  future change of route. `--load-media` (or `TIPRANKS_LOAD_MEDIA=1`)
  loads them. Without a proxy, or over `--cdp-endpoint`, nothing is
  blocked.

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
| 4 | zero results (every ticker answered "not found") |
| 5 | no rows obtained: navigation/network failure, a proxy that refused the credential or the balance before the run began (`stop_reason: proxy_auth_failed` / `proxy_balance_exhausted` in `<out>.latest_attempt.meta.json`), or a served payload the parser could not read (status `parser_failed`) |
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
failure. A served payload whose stock list no longer contains the
ticker's record (or has no stock list at all) is a parser failure
instead: the ticker counts as failed and is listed under
`parser_failed_tickers` in the sidecar, so a page-shape change is never
reported as "this ticker does not exist". CSV output prefixes text cells
that start with `=`, `+`, `-`, `@`, a tab or a line break with `'` so a
spreadsheet does not run them as formulas (JSON is never altered); the
sidecar's `csv_cells_escaped` counts them. New output files get the
permissions your umask allows; an existing file keeps its mode.

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
as exit 3. GitHub Actions' own datacentre IPs are refused the same way:
every scheduled canary run from 2026-10-01 to 2026-10-15 ended blocked
with 0 rows. The canary's `canary-live` job therefore runs through the
`TIPRANKS_PROXY` or `TIPRANKS_CDP_ENDPOINT` repository secret, weekly
(Mondays) and on a manual dispatch, and is red
unless it collects real, fully parsed rows; without either secret it
fails with an error naming them. A separate `canary-datacentre` job keeps
trying the runner's own IP and reports the expected refusal as a notice.

## Engine notes

- **Every engine navigates straight to `payload.json`**, never the
  ticker page. Live on 2026-10-19 Cloudflare challenged the ticker page
  (403, "Just a moment…") from a home IP, a VPN and a residential proxy,
  with or without `--fingerprint`, while the payload answered 200 from
  all three: a plain local run then collected 30 of 30 tickers.
- **Playwright / Puppeteer** read the real HTTP status of that response
  to detect an unknown ticker (a real 404/400), and also check the
  confirmed Cloudflare block markers (`tipranks_parser.
  BLOCK_URL_MARKERS`/`BLOCK_BODY_MARKERS`) against the response/URL. Both are also the only two engines that can open an
  authenticated `--cdp-endpoint` session (e.g. the 2Captcha Scraping
  Browser API) and arm its own `Captcha.setAutoSolve` CDP domain.
- **Selenium**: chromedriver doesn't expose the response's HTTP
  status, so `selenium_scraper.py` reads the site's own JSON answer
  instead (`{"msg": "ticker not found"}` is "not found") and detects a
  block from the same URL/body markers only. `--cdp-endpoint` is refused outright when it carries credentials
  — see "2Captcha toolkit" above.
- **Puppeteer (legacy/experimental)**: pyppeteer is effectively unmaintained.
  Confirmed live on a real Apple Silicon Mac — pyppeteer's
  own bundled Chromium download (pinned at revision 117.0.5938.0)
  launches but then segfaults on an actual headless run. Playwright's and
  Selenium's own browser/driver downloads were unaffected on the same
  machine, so this is specific to that one pinned build, not this site or
  this repo's code. On Apple Silicon the engine now finds Google Chrome
  or Chromium in `/Applications` or `~/Applications` and uses it on its
  own (logged at INFO). The bundled build is still tried when neither is
  installed, and on every other platform, including the Docker image.
  `--executable-path /path/to/Chrome` overrides the search (all three
  engines accept this flag, for CLI parity, though only Puppeteer has
  needed it so far).

## Testing

`python3 smoke_test.py` — offline, no browser required, runs against
real captured fixtures in `tests/fixtures/`, and against all three
engines with no driver installed. See `TESTING.md` for the full coverage
list and the live-testing checklist.

## Known limitations

- No confirmed CAPTCHA widget on this site — `captcha_solver.py`'s
  solving plumbing is shipped for family parity and may simply find
  nothing to solve; see "2Captcha toolkit" above.
- The ticker page itself sits behind a Cloudflare challenge that this
  repo does not solve (it is Cloudflare's own challenge page, not a
  Turnstile widget on the site); the engines avoid it by reading
  `payload.json` directly. If Cloudflare ever puts the payload behind the
  same challenge, local runs will exit 3; `--cdp-endpoint` solves it on
  2Captcha's side.
- No rate limiting seen so far: on 2026-10-20 the whole S&P 500 (503
  tickers) ran three times back to back from one home IP with
  Playwright, about 1,500 requests in 8 minutes, one ticker after
  another with no pause. Every run: 503 of 503, exit 0, no 403/429,
  125–165 s each. Running several engines or machines in parallel was
  not tested.
- Only the ticker forecast page. No screener/stock-list pages, and no
  per-transaction insider or hedge fund lists: only the summaries the
  ticker's own payload carries (above).
- `enumId` 1 (presumably `"strongSell"`) was never observed live; the
  parser never hardcodes a guess for it — it reads the site's own label
  string directly instead of a private lookup table.
- Untested: non-USD tickers, and a `-` in a ticker symbol (a `.`, e.g.
  `BRK.B`, is confirmed live).
- No `local_e2e_test.py` — see `TESTING.md` for why a single ticker fetch
  doesn't need one the way a paginated, proxy-rotating scraper does.
