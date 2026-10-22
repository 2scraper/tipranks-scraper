#!/usr/bin/env python3
"""canary_endpoint.py -- choose the exit for the canary-live job and put a
CDP endpoint, if any, into .env (mode 0600). Never prints one.

Order:
  1. secret TWOCAPTCHA_KEY + repository variable TIPRANKS_BROWSER_ACCOUNT_ID:
     the account's fresh connectionUri from GET /browser/accounts
     (tools/browser_endpoint.py), fetched right before the run;
  2. otherwise the TIPRANKS_CDP_ENDPOINT secret, as before;
  3. otherwise no CDP endpoint, and the run goes through TIPRANKS_PROXY.

Exits 1 with ::error:: naming what is missing when none of these is usable,
and also when the fresh fetch was configured but failed and there is no
stored endpoint to fall back on: running through the proxy instead would
hide that the configured path is broken.

Inputs (environment): TWOCAPTCHA_KEY, ACCOUNT_ID, STORED_CDP_ENDPOINT,
TIPRANKS_PROXY. Output: the GitHub step output `cdp_source` (fresh, secret
or none) when GITHUB_OUTPUT is set.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import browser_endpoint  # noqa: E402

FRESH = "secret TWOCAPTCHA_KEY and repository variable TIPRANKS_BROWSER_ACCOUNT_ID"


def _set_output(source: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"cdp_source={source}\n")


def choose(env_file: Path, environ=None, out=None, base=None, opener=None) -> int:
    environ = os.environ if environ is None else environ
    out = out or sys.stdout
    key = (environ.get("TWOCAPTCHA_KEY") or "").strip()
    account = (environ.get("ACCOUNT_ID") or "").strip()
    stored = (environ.get("STORED_CDP_ENDPOINT") or "").strip()
    proxy = (environ.get("TIPRANKS_PROXY") or "").strip()

    fresh_failed = False
    if key and account:
        kwargs = {"opener": opener, "key": key}
        if base:
            kwargs["base"] = base
        rc = browser_endpoint.run(account, env_file, github_mask=True, out=out,
                                  **kwargs)
        if rc == browser_endpoint.EXIT_OK:
            print(f"canary endpoint: fresh connectionUri of browser account "
                  f"{account}", file=out)
            _set_output("fresh")
            return 0
        fresh_failed = True
        print(f"::warning::the fresh connectionUri of browser account {account} "
              f"was not fetched (tools/browser_endpoint.py exit {rc}, reason "
              f"above).", file=out)
    else:
        missing = [n for n, v in (("secret TWOCAPTCHA_KEY", key),
                                  ("repository variable TIPRANKS_BROWSER_ACCOUNT_ID",
                                   account)) if not v]
        print(f"::notice::no fresh Scraping Browser endpoint: "
              f"{' and '.join(missing)} not set.", file=out)

    if stored:
        browser_endpoint.write_env(env_file, stored)
        print("canary endpoint: the stored TIPRANKS_CDP_ENDPOINT secret"
              + (" (fallback; it may have expired)" if fresh_failed else ""),
              file=out)
        _set_output("secret")
        return 0
    if fresh_failed:
        print(f"::error::canary NOT RUN: the fresh connectionUri could not be "
              f"fetched (see above) and the TIPRANKS_CDP_ENDPOINT secret is not "
              f"set to fall back on. Check the {FRESH}.", file=out)
        return 1
    if proxy:
        print("::notice::no CDP endpoint; the run goes through the "
              "TIPRANKS_PROXY secret.", file=out)
        _set_output("none")
        return 0
    print(f"::error::canary NOT RUN: no usable exit. A GitHub runner's own IP is "
          f"refused by tipranks.com. Set the {FRESH} (a fresh connectionUri per "
          f"run), or the TIPRANKS_CDP_ENDPOINT secret, or the TIPRANKS_PROXY "
          f"secret. None of them is set.", file=out)
    return 1


if __name__ == "__main__":
    sys.exit(choose(ROOT / ".env"))
