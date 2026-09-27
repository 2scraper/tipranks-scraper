# Security

## Supported versions

Only the latest commit on `main` and the most recently tagged release get
security fixes. This project is pre-1.0 (see `CHANGELOG.md`) — older tags
are not backported.

## Reporting a vulnerability

Please report security issues privately — open a GitHub security advisory
on this repo, or email support@2captcha.com — rather than a public issue.
Include the version/commit and a minimal reproduction.

We aim to acknowledge a new report within 5 business days, confirm whether
it's in scope, and share a fix timeline once it's confirmed. If a report
turns out to be a real, exploitable issue, we'll credit the reporter in
`CHANGELOG.md` unless they'd rather stay anonymous.

## Scope

**In scope**: this repo's own code and dependencies — injection risks,
anything that would make this tool execute untrusted code, or misreport
what it actually did (a `complete` status on a run that silently dropped
data would count, for example).

**Out of scope**: vulnerabilities in tipranks.com itself — report those to
TipRanks directly, not here. Its edge/bot-mitigation stack (Fastly,
Cloudflare, an unrendered Google reCAPTCHA script — see
`tipranks_parser.py`'s module docstring) and how it does or doesn't react
to traffic is documented site behavior this repo reacts to, not a
vulnerability in this repo.

## What this tool does with credentials

**None.** This is the one thing genuinely different from this project's
sibling scrapers: the ticker forecast page is free, public data with no
login, so there is no proxy, no API key, no CDP endpoint, and no `.env`
anywhere in this repo. There is nothing here to leak.

- This project's own code doesn't phone home. The only network calls it
  makes are to `tipranks.com`.
- This is a scraper, not an account tool: it never logs in, never
  touches an authenticated session, and never writes anything back to
  tipranks.com.
- **One caveat on the Selenium engine**: Selenium's own bundled Selenium
  Manager sends anonymous usage stats to `plausible.io` by default
  whenever it launches a browser, which `selenium_scraper.py` disables on
  your behalf (`SE_AVOID_STATS=true`, set as a default rather than
  forced, so it never overrides a value you set yourself) so this
  project's "nothing phones home" claim actually holds. Selenium Manager
  can still make a *separate* network call to resolve a matching
  `chromedriver` version if one isn't already reachable on `PATH` — set
  `SELENIUM_CHROME_BIN` to a Chrome/Chromium binary you already have to
  avoid it entirely.
- **robots.txt**: `tipranks_parser.is_allowed()` re-derives its answer
  from a captured copy of `https://www.tipranks.com/robots.txt` at
  runtime rather than a hardcoded conclusion — see that module's
  docstring for what's actually in the captured text and why the ticker
  paths this repo uses are allowed under it.
