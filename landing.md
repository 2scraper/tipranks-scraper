# TipRanks Scraper by 2scraper

**Open-source stock-ticker forecast scraper for tipranks.com — three engines, free public data, no login, no proxy, no captcha solving.**

Pull a stock ticker's Smart Score, analyst consensus (Buy/Hold/Sell),
and price target straight into JSON or CSV.

[**View source on GitHub →**](https://github.com/2scraper/tipranks-scraper)

---

## What you get

- Free, open-source scraper, one script per engine — **Playwright**
  (primary, local-first), **Selenium**, and **Puppeteer** (via
  pyppeteer), all producing the identical output schema and exit codes
- Reads the ticker page's own same-origin JSON endpoint
  (`stock-analysis/payload.json`) directly — a real, confirmed API
  response, not DOM/CSS scraping
- Smart Score, the "all analysts" consensus and the "best performing
  analysts" consensus kept as two separate, real analyst populations,
  never merged into one
- Price target average/high/low/upside, buy/hold/sell counts, a
  best-effort current price
- JSON and CSV export, with a documented `TickerRating` schema, atomic file
  replacement, and a checksummed `.meta.json` sidecar
- robots.txt honoured, re-checked from the real captured text at
  runtime rather than a hardcoded conclusion
- **No credentials of any kind.** No proxy, no API key, no CDP endpoint,
  no `.env` — this ticker page is free, public data

## Honest about one thing

**Bot protection.** tipranks.com sits behind Fastly (caching) and
Cloudflare (a bot-management fingerprinting script loads on every page
view, and Google reCAPTCHA's script is present but never auto-rendered).
Neither challenged the original research session, but a later audit
observed a real one: an HTTP 403 Cloudflare JS challenge ("Just a
moment...") on the page navigation, in a local headless Playwright run,
after an earlier run had already completed successfully — behavior-
triggered, not a first-request block. The scraper reports that state as
exit 3 and the daily live canary treats it as unavailable rather than
leaving a misleading green badge. See `tipranks_parser.py`'s module
docstring for the full detail.

## Who this is for

Anyone tracking how a stock's analyst consensus or price target moves
over time, and anyone who wants TipRanks' ticker-page data in a script
rather than a browser tab.

## Get started

```bash
git clone https://github.com/2scraper/tipranks-scraper.git
cd tipranks-scraper
pip install -r requirements-playwright.txt && playwright install chromium

python3 playwright_scraper.py --ticker AAPL --ticker MSFT --format json --out results.json
```

Full setup and CLI reference in the [repository README](https://github.com/2scraper/tipranks-scraper#readme).

---

**Need it running at scale?**
[Talk to us →](https://2captcha.com/contact)
