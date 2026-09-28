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

**In scope**: this repo's own code and dependencies — credential
handling (added 2026-09-28, see below), injection risks, anything that
would make this tool leak a secret, execute untrusted code, or misreport
what it actually did (a `complete` status on a run that silently dropped
data would count, for example).

**Out of scope**: vulnerabilities in tipranks.com itself — report those to
TipRanks directly, not here. Its edge/bot-mitigation stack (Fastly,
Cloudflare, an unrendered Google reCAPTCHA script — see
`tipranks_parser.py`'s module docstring) and how it does or doesn't react
to traffic is documented site behavior this repo reacts to, not a
vulnerability in this repo.

## What this tool does with credentials

**Optional, and only since 2026-09-28.** The ticker forecast page itself
is still free, public data with no login required for a normal run (see
README "Local-first") — but this repo now carries the same credential
model every other family member does, added after a real Cloudflare
block was observed in a pre-release audit: `TWOCAPTCHA_KEY`,
`TIPRANKS_PROXY`, `TIPRANKS_CDP_ENDPOINT`. All three are opt-in.

- `TWOCAPTCHA_KEY`/`TIPRANKS_PROXY`/`TIPRANKS_CDP_ENDPOINT` are read only
  from `.env` or the environment (see `env_config.py`) — never from the
  command line, and never logged in full. `proxy_pool.py` masks a
  proxy's password (not its login) in every log line, and its
  `redact_credentials()` scrubs a credential out of an arbitrary
  driver/HTTP exception message before it's logged or re-raised — an
  exception message is a log too (CLAUDE.md §8): Playwright's
  `connect_over_cdp` repeats a failed `ws://login:password@…` endpoint
  up to five times in one error, and `requests` puts the full URL, query
  string included, into every `HTTPError`.
- This project's own code doesn't phone home beyond what you configure.
  The only network calls it makes are to `tipranks.com` and, only when
  you set a key/proxy/endpoint, `api.2captcha.com` / `cb.2captcha.com` /
  `scraper.2captcha.com`. **One caveat on the Selenium engine**:
  Selenium's own bundled Selenium Manager sends anonymous usage stats to
  `plausible.io` by default whenever it launches a browser, which
  `selenium_scraper.py` disables on your behalf (`SE_AVOID_STATS=true`,
  set as a default rather than forced, so it never overrides a value you
  set yourself) so this project's "nothing phones home" claim actually
  holds. Selenium Manager can still make a *separate* network call to
  resolve a matching `chromedriver` version if one isn't already
  reachable on `PATH`. `--executable-path` selects an installed
  Chrome/Chromium binary, but does not itself provide chromedriver; put
  a compatible chromedriver on `PATH` if Selenium Manager must not
  access the network.
- This is a scraper, not an account tool: it never logs in, never
  touches an authenticated session, and never writes anything back to
  tipranks.com.
- **Selenium cannot authenticate a remote CDP session at all** —
  `--cdp-endpoint` is refused outright (`EXIT_BAD_USAGE`) when it carries
  credentials, rather than silently dropping them or half-connecting.
  Its `--proxy-server` Chrome flag also cannot authenticate; a `--proxy`
  with a login/password has its credentials stripped before reaching
  Chrome, with a logged warning.
- Never stack a `--fingerprint` profile on top of a `--cdp-endpoint`
  session — the Scraping Browser API already brings its own device
  identity (`fingerprint_client.refuse_if_cdp` is the shared guard for
  this, same as every sibling repo).
- **One caveat on the Selenium engine**: Selenium's own bundled Selenium
  Manager sends anonymous usage stats to `plausible.io` by default
  whenever it launches a browser, which `selenium_scraper.py` disables on
  your behalf (`SE_AVOID_STATS=true`, set as a default rather than
  forced, so it never overrides a value you set yourself) so this
  project's "nothing phones home" claim actually holds. Selenium Manager
  can still make a *separate* network call to resolve a matching
  `chromedriver` version if one isn't already reachable on `PATH`.
  `--executable-path` selects an installed Chrome/Chromium binary, but does
  not itself provide chromedriver; put a compatible chromedriver on `PATH`
  if Selenium Manager must not access the network.
- **robots.txt**: `tipranks_parser.is_allowed()` re-derives its answer
  from a captured copy of `https://www.tipranks.com/robots.txt` at
  runtime rather than a hardcoded conclusion — see that module's
  docstring for what's actually in the captured text and why the ticker
  paths this repo uses are allowed under it.
