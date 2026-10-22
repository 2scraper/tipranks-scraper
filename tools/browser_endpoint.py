#!/usr/bin/env python3
"""browser_endpoint.py -- put a browser account's CURRENT Scraping Browser
connection string into .env, fetched from the 2Captcha API.

    TWOCAPTCHA_KEY=... python3 tools/browser_endpoint.py --account-id 1581

Why: a connection string stored once as a secret stops working. The one in
this repo's canary was accepted on 2026-10-19 and 2026-10-20 and refused
with `401 deny_no_user` on 2026-10-21. `GET /browser/accounts` lists every
browser account of the key with a ready `connectionUri` (and one per
profile), so the canary asks for it right before each run instead.

What it does:
  * reads the key from TWOCAPTCHA_KEY (environment or .env), never argv;
  * picks the account whose `id` is --account-id, and takes its profile's
    `connectionUri` when the profile has one, else the account's own;
  * writes `TIPRANKS_CDP_ENDPOINT=<uri>` into --env-file (default: .env next
    to the scripts), mode 0600, replacing an existing line;
  * prints only the masked form (scheme://***:***@host:port) and the length.

The key rides in the QUERY STRING of this GET, and a URL ends up in error
texts. Every message here passes through redact(), which removes the key
wherever it appears. On an HTTP error the API's own errorCode and
errorDescription are printed (redacted), not a bare status.

Exit codes: 0 written; 2 bad usage (no key, no or malformed account id);
3 no such account in the listing; 4 the API call failed (transport, HTTP
error, non-JSON, an error status); 5 the account has no usable connectionUri.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import env_config  # noqa: E402

BASE = "https://api.2captcha.com"
PATH = "/browser/accounts"
VAR = "TIPRANKS_CDP_ENDPOINT"
TIMEOUT = 30

EXIT_OK, EXIT_USAGE, EXIT_NO_ACCOUNT, EXIT_API, EXIT_NO_URI = 0, 2, 3, 4, 5

_QUERY_SECRET_RE = re.compile(r"(?i)\b(key|token|password)=([^&\s\"']+)")
_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@\"']+@")


class EndpointError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def redact(text, *secrets: str) -> str:
    """`text` with the key, any query-string secret and any URL userinfo
    removed. Never raises."""
    try:
        out = str(text)
    except Exception:  # noqa: BLE001
        return "(unprintable, redacted)"
    for value in secrets:
        if value:
            out = out.replace(value, "***")
            quoted = urllib.parse.quote(value, safe="")
            if quoted != value:
                out = out.replace(quoted, "***")
    out = _QUERY_SECRET_RE.sub(r"\1=***", out)
    return _USERINFO_RE.sub(r"\1***:***@", out)


def mask_uri(uri: str) -> str:
    """scheme://***:***@host:port. Never raises."""
    try:
        p = urllib.parse.urlparse(uri)
        try:
            port = f":{p.port}" if p.port else ""
        except ValueError:
            port = ":?"
        creds = "***:***@" if (p.username or p.password) else ""
        return f"{p.scheme or '?'}://{creds}{p.hostname or '?'}{port}"
    except Exception:  # noqa: BLE001
        return "(unparseable URI, redacted)"


def _error_detail(body: bytes, key: str) -> str:
    """The API's errorCode / errorDescription, or a short safe description."""
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return f"non-JSON body, {len(body)} bytes"
    if isinstance(data, dict):
        parts = [f"{f}={data[f]}" for f in ("errorCode", "errorDescription",
                                             "status", "errorId", "message")
                 if data.get(f) not in (None, "")]
        if parts:
            return redact(" ".join(parts), key)
    return redact(json.dumps(data)[:300], key)


def _get(url: str, opener=None):
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with opener(req, timeout=TIMEOUT) as resp:
        return resp.read()


def fetch_accounts(key: str, base: str = BASE, opener=None):
    url = f"{base}{PATH}?" + urllib.parse.urlencode({"key": key})
    try:
        body = _get(url, opener)
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()
        except Exception:  # noqa: BLE001
            raw = b""
        raise EndpointError(EXIT_API, f"GET {PATH} failed: HTTP {e.code}: "
                            f"{_error_detail(raw, key)}") from None
    except Exception as e:  # noqa: BLE001 -- URLError, timeouts, TLS ...
        raise EndpointError(EXIT_API, f"GET {PATH} failed: "
                            f"{type(e).__name__}: {redact(e, key)}") from None
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        raise EndpointError(EXIT_API, f"GET {PATH}: the response was not "
                            f"JSON ({len(body)} bytes)") from None
    if isinstance(data, dict) and (
            data.get("status") not in (None, "OK", 1)
            or data.get("errorId") not in (None, 0)):
        raise EndpointError(EXIT_API, f"GET {PATH} returned "
                            f"{_error_detail(body, key)}")
    return data


def _dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _dicts(v)


def pick_uri(data, account_id: str) -> tuple:
    """(uri, account) for `account_id`. Prefers the profile's connectionUri."""
    account = next((d for d in _dicts(data)
                    if str(d.get("id")) == str(account_id)
                    and ("connectionUri" in d or "profile" in d)), None)
    if account is None:
        ids = sorted({str(d["id"]) for d in _dicts(data)
                      if "id" in d and ("connectionUri" in d or "profile" in d)})
        raise EndpointError(EXIT_NO_ACCOUNT,
                            f"no browser account with id {account_id} for this "
                            f"key (ids listed: {', '.join(ids) or 'none'})")
    profile = account.get("profile") if isinstance(account.get("profile"), dict) else {}
    for uri in (profile.get("connectionUri"), account.get("connectionUri")):
        if isinstance(uri, str) and "://" in uri:
            return uri, account
    raise EndpointError(EXIT_NO_URI, f"browser account {account_id} carries no "
                        f"usable connectionUri")


def write_env(path: Path, uri: str, var: str = VAR) -> None:
    """Replace or add `var=uri` in `path`, atomically, mode 0600."""
    lines = []
    if path.is_file():
        lines = [l for l in path.read_text(encoding="utf-8").splitlines()
                 if not re.match(rf"\s*(export\s+)?{re.escape(var)}\s*=", l)]
    lines.append(f"{var}={uri}")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _key_from_env() -> str:
    """TWOCAPTCHA_KEY from the environment, else from .env beside the
    scripts; an empty or placeholder value counts as unset."""
    value = os.environ.get("TWOCAPTCHA_KEY")
    if value is None:
        value = env_config.load_dotenv_values(str(ROOT / ".env")).get("TWOCAPTCHA_KEY")
    return "" if env_config._is_placeholder(value) else value.strip()


def run(account_id: str, env_file: Path, github_mask: bool = False,
        base: str = BASE, opener=None, out=None, key: Optional[str] = None) -> int:
    out = out or sys.stdout
    if key is None:
        key = _key_from_env()
    try:
        if not key:
            raise EndpointError(EXIT_USAGE, "TWOCAPTCHA_KEY is not set (or is "
                                "the .env.example placeholder)")
        if not re.fullmatch(r"\d+", account_id or ""):
            raise EndpointError(EXIT_USAGE, "--account-id must be the browser "
                                "account's numeric id")
        uri, account = pick_uri(fetch_accounts(key, base, opener), account_id)
    except EndpointError as e:
        print(f"browser_endpoint: {redact(e, key)}", file=sys.stderr)
        return e.code
    if github_mask:
        # The workflow-command form; the runner hides these values in every
        # later line of the log and does not print this one.
        p = urllib.parse.urlparse(uri)
        for value in (uri, p.username, p.password):
            if value:
                print(f"::add-mask::{value}", file=out)
    write_env(env_file, uri)
    profile = account.get("profile") if isinstance(account.get("profile"), dict) else {}
    source = "profile" if profile.get("connectionUri") == uri else "account"
    print(f"browser account {account_id} (proxyMode={account.get('proxyMode')!r}): "
          f"{source} connectionUri {mask_uri(uri)} ({len(uri)} chars) "
          f"written to {env_file.name} as {VAR}", file=out)
    return EXIT_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--account-id", required=True,
                    help="numeric id of the 2Captcha browser account")
    ap.add_argument("--env-file", default=str(ROOT / ".env"),
                    help="file to write TIPRANKS_CDP_ENDPOINT into (mode 0600)")
    ap.add_argument("--github-mask", action="store_true",
                    help="emit ::add-mask:: for the URI and its login/password "
                         "(GitHub Actions)")
    args = ap.parse_args(argv)
    return run(args.account_id.strip(), Path(args.env_file), args.github_mask)


if __name__ == "__main__":
    sys.exit(main())
