"""
proxy_check.py
---------------
Two traffic savers for runs that go out through a local proxy.

A residential proxy is billed by the byte, and both of these exist because
of what a run used to spend before it produced anything:

1. **A pre-check before any browser starts.** Chromium does not report a
   proxy's 407 as a 407. A rejected credential surfaced as
   `Page.goto: Timeout ... exceeded`, retried, rotated and timed out again,
   so a run with a wrong or exhausted proxy login spent minutes on retries
   and ended "page_load_timeout", which points at the site. One CONNECT
   through the proxy, followed by a HEAD for the target's own robots.txt,
   names the real cause in a second or two:

     407  the gateway refused the credential (or the traffic balance is
          exhausted, which some gateways also answer with 407)
     402  the proxy's traffic or balance is exhausted

   Both end the run before a browser is launched: exit 5 (content not
   obtained) and `<out>.latest_attempt.meta.json` with `status: failed` and
   `stop_reason: proxy_auth_failed` / `proxy_balance_exhausted`. That file
   is separate from `<out>.meta.json` on purpose: a failed attempt must not
   overwrite the metadata of the previous run's good output, which `save`
   leaves in place.

   Anything else, including a transport error (the proxy host does not
   answer, DNS fails, TLS breaks), is logged and the run goes on exactly as
   it did before this check existed: the browser then makes its own attempt
   and the existing retry and classification rules apply. Only an answer the
   proxy gave about the credential or the balance is treated as final.

   The target is the site's robots.txt rather than a third-party "what is my
   IP" service: it is tiny, it is on the host the run is about to fetch
   anyway, and it does not send the run's traffic to anyone else.

2. **Media blocking.** Images, video/audio and web fonts are most of the
   bytes of a page and none of the data this scraper reads. When (and only
   when) the browser's traffic goes through a local proxy, requests of those
   resource types are aborted. Not over --cdp-endpoint (the remote browser
   has its own exit and its own billing, and is not ours to rewrite), and
   not without a proxy (nothing is metered). `--load-media` (or
   `<SITE>_LOAD_MEDIA=1`) turns it off.

The credential is never printed: every message names the proxy as
`scheme://***:***@host:port`, with neither the login nor the password.

This repo's engines take a `proxy_pool.Proxy` (host, port, login,
password) rather than a URL string, so the pre-check reads those fields
directly and sends exactly the login and password the browser would.
"""

from __future__ import annotations

import base64
import logging
import socket
import ssl
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import urlparse


logger = logging.getLogger("proxy_check")

# Where the proxy credential comes from, for the failure message. Named
# rather than "your proxy" because the person reading the log is often
# looking at a CI secret, not at a command line.
PROXY_SOURCES = "TIPRANKS_PROXY (or --proxy / --proxy-file)"
LOAD_MEDIA_ENV = "TIPRANKS_LOAD_MEDIA"

# Every engine navigates straight to `payload.json` and parses that JSON;
# no image, video or font is part of the data. Measured (README, "Proxy
# traffic savers"): a run with these blocked produced the same rows and
# values as one without.
BLOCK_MEDIA_BY_DEFAULT = True

PRECHECK_TIMEOUT = 15.0

STOP_AUTH_FAILED = "proxy_auth_failed"
STOP_BALANCE_EXHAUSTED = "proxy_balance_exhausted"

# Playwright's `request.resource_type` names, and the same three as CDP's
# Fetch domain spells them (pyppeteer goes through that domain directly).
BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})
CDP_BLOCKED_RESOURCE_TYPES = ("Image", "Media", "Font")

# Selenium has no request interception, but Chromium's CDP
# `Network.setBlockedURLs` takes wildcard URL patterns. Extensions are a
# coarser test than a resource type, so the list is limited to formats that
# are only ever media; a trailing `*` also catches `?width=...` variants.
BLOCKED_URL_PATTERNS = tuple(
    f"*.{ext}{tail}"
    for ext in ("png", "jpg", "jpeg", "gif", "webp", "avif", "bmp", "ico",
                "svg", "mp4", "webm", "m4v", "mov", "m3u8", "mp3", "m4a",
                "ogg", "wav", "woff", "woff2", "ttf", "otf", "eot")
    for tail in ("", "?*"))

_FALSE_WORDS = {"", "0", "false", "no", "off"}


@dataclass(frozen=True)
class ProxyCheck:
    """What the pre-check learned. `fatal` is True only for 407 and 402."""

    outcome: str            # ok | auth_failed | balance_exhausted | transport_error | skipped
    status: Optional[int]   # the proxy's (or, through the tunnel, the site's) status
    detail: str = ""

    @property
    def fatal(self) -> bool:
        return self.outcome in ("auth_failed", "balance_exhausted")

    @property
    def stop_reason(self) -> Optional[str]:
        return {"auth_failed": STOP_AUTH_FAILED,
                "balance_exhausted": STOP_BALANCE_EXHAUSTED}.get(self.outcome)


def _read_head(sock, limit: int = 65536) -> Tuple[int, str]:
    """Read an HTTP response head; return (status, reason)."""
    buf = b""
    while b"\r\n\r\n" not in buf and b"\n\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > limit:
            break
    line = buf.split(b"\n", 1)[0].decode("latin-1").strip()
    parts = line.split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
        raise ValueError(f"not an HTTP response: {line[:60]!r}")
    return int(parts[1]), (parts[2] if len(parts) > 2 else "")


def mask(proxy) -> str:
    """`scheme://***:***@host:port` (or without the stars when there is no
    login). Neither the login nor the password is shown."""
    if proxy is None:
        return "(none)"
    creds = "***:***@" if getattr(proxy, "login", None) else ""
    return f"{proxy.scheme}://{creds}{proxy.host}:{proxy.port}"


def _auth_header(proxy) -> str:
    # The login and password are sent exactly as the engines hand them to
    # the browser (Proxy.playwright_proxy_dict / pyppeteer_auth_dict), so
    # the check judges the same credential the browser would present.
    if not proxy.login:
        return ""
    token = base64.b64encode(
        f"{proxy.login}:{proxy.password or ''}".encode()).decode()
    return f"Proxy-Authorization: Basic {token}\r\n"


def _classify(status: int, reason: str) -> Optional[ProxyCheck]:
    if status == 407:
        return ProxyCheck("auth_failed", 407, reason)
    if status == 402:
        return ProxyCheck("balance_exhausted", 402, reason)
    return None


def precheck(proxy, target_url: str,
             timeout: float = PRECHECK_TIMEOUT) -> ProxyCheck:
    """One request through `proxy` (a proxy_pool.Proxy): a HEAD for
    `target_url`'s robots.txt.

    For an https target that is a CONNECT to the target host, then the HEAD
    inside the tunnel. A 407 or 402 is the proxy's own answer about the
    credential or the balance; the site never sees the request. Any status
    the SITE gives (a 403 to a datacentre exit, a 404 for a missing
    robots.txt) is not this check's business and counts as `ok`: the proxy
    let the request through.
    """
    if proxy is None:
        return ProxyCheck("skipped", None, "no proxy")
    if proxy.scheme not in ("http", "https"):
        # SOCKS5 carries no credentials in any engine here (proxy_pool
        # refuses an authenticated socks5 entry), so there is nothing to
        # check that the browser would not report itself.
        return ProxyCheck("skipped", None, f"{proxy.scheme} proxy")
    target = urlparse(target_url or "")
    if target.scheme not in ("http", "https") or not target.hostname:
        return ProxyCheck("skipped", None, "no http(s) target URL")
    proxy_port = proxy.port
    try:
        target_port = target.port or (443 if target.scheme == "https" else 80)
    except ValueError:
        return ProxyCheck("skipped", None, "unparseable port")
    host_header = (target.hostname if target.port is None
                   else f"{target.hostname}:{target.port}")
    auth = _auth_header(proxy)
    sock = None
    try:
        sock = socket.create_connection((proxy.host, proxy_port),
                                        timeout=timeout)
        if proxy.scheme == "https":
            sock = ssl.create_default_context().wrap_socket(
                sock, server_hostname=proxy.host)
        if target.scheme == "https":
            sock.sendall((f"CONNECT {target.hostname}:{target_port} HTTP/1.1\r\n"
                          f"Host: {target.hostname}:{target_port}\r\n"
                          f"{auth}\r\n").encode())
            status, reason = _read_head(sock)
            verdict = _classify(status, reason)
            if verdict:
                return verdict
            if status != 200:
                # The proxy refused the tunnel for another reason (a 403 for
                # a blocked port, a 502 from an upstream). Not about the
                # credential, so not final: the browser gets its own try.
                return ProxyCheck("transport_error", status,
                                  f"CONNECT answered {status} {reason}".strip())
            # The tunnel is up, so the proxy accepted the credential: that
            # is the verdict. The HEAD below only completes the one request;
            # whatever the site does with it (a refusal, a reset) is the
            # browser's business and does not change the answer.
            if proxy.scheme == "https":
                # TLS inside TLS is not something the ssl module does on a
                # wrapped socket; the CONNECT answer already says enough.
                return ProxyCheck("ok", None, "tunnel established")
            try:
                tls = ssl.create_default_context().wrap_socket(
                    sock, server_hostname=target.hostname)
                sock = tls
                sock.sendall((f"HEAD /robots.txt HTTP/1.1\r\n"
                              f"Host: {host_header}\r\n"
                              "User-Agent: Mozilla/5.0\r\nAccept: */*\r\n"
                              "Connection: close\r\n\r\n").encode())
                status, reason = _read_head(sock)
            except (OSError, ssl.SSLError, ValueError) as e:
                return ProxyCheck("ok", None,
                                  f"tunnel established; site: {type(e).__name__}")
            return ProxyCheck("ok", status, reason)
        sock.sendall((f"HEAD http://{host_header}/robots.txt HTTP/1.1\r\n"
                      f"Host: {host_header}\r\n{auth}"
                      "User-Agent: Mozilla/5.0\r\nAccept: */*\r\n"
                      "Connection: close\r\n\r\n").encode())
        status, reason = _read_head(sock)
        return _classify(status, reason) or ProxyCheck("ok", status, reason)
    except (OSError, ssl.SSLError, ValueError) as e:
        return ProxyCheck("transport_error", None, type(e).__name__)
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def failure_message(check: ProxyCheck, proxy) -> str:
    """The one-line explanation for a fatal pre-check. Never holds the login."""
    shown = mask(proxy)
    if check.outcome == "auth_failed":
        return (f"Proxy pre-check: {shown} answered HTTP 407 — the gateway "
                f"refused the credential (or the traffic balance is "
                f"exhausted). Check {PROXY_SOURCES} and the proxy account. "
                f"No browser was started and nothing was fetched.")
    return (f"Proxy pre-check: {shown} answered HTTP 402 — proxy "
            f"traffic/balance exhausted. Top up the proxy account or change "
            f"{PROXY_SOURCES}. No browser was started and nothing was fetched.")


def gate(pool, target_url: str, timeout: float = PRECHECK_TIMEOUT
         ) -> Optional[ProxyCheck]:
    """Run the pre-check on the exit the run will use; return it if fatal.

    One request for the whole run, on `pool.peek()`: the proxy the engine's
    own `pool.next()` is about to hand out, without moving the cursor. A
    pool's other exits share the account, so one refusal speaks for all.
    """
    if not pool:
        return None
    exit_url = pool.peek()
    if exit_url is None:
        return None
    check = precheck(exit_url, target_url, timeout=timeout)
    if check.fatal:
        logger.error("%s", failure_message(check, exit_url))
        return check
    if check.outcome == "transport_error":
        logger.warning("Proxy pre-check through %s did not complete (%s); "
                       "continuing, the browser will make its own attempt.",
                       mask(exit_url), check.detail)
    elif check.outcome == "ok":
        logger.info("Proxy pre-check through %s: accepted (%s).",
                    mask(exit_url),
                    f"site answered {check.status}" if check.status
                    else check.detail)
    return None


# ---------------------------------------------------------------------------
# Media blocking
# ---------------------------------------------------------------------------

def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in _FALSE_WORDS
    return bool(value)


def block_media(args, pool, remote: bool) -> bool:
    """Whether this run should abort image/media/font requests.

    `--block-media` (hidden; for tests and measurements) forces it on even
    without a proxy. Otherwise: only through a local proxy, only when the
    site's default allows it, and never when `--load-media` /
    `TIPRANKS_LOAD_MEDIA` asks for media.
    """
    if remote:
        return False
    if getattr(args, "block_media", False):
        return True
    if _truthy(getattr(args, "load_media", False)):
        return False
    return BLOCK_MEDIA_BY_DEFAULT and bool(pool)


def add_media_flags(parser) -> None:
    """`--load-media` (public) and `--block-media` (hidden test switch)."""
    import argparse
    group = parser.add_mutually_exclusive_group()
    # default=None, not False: env_config.apply_env fills a destination only
    # while it is still None, so TIPRANKS_LOAD_MEDIA could never apply to a
    # False default.
    group.add_argument(
        "--load-media", action="store_true", default=None,
        help=("Load images, video/audio and fonts through the proxy. By "
              "default they are blocked whenever a local --proxy/--proxy-file "
              "is in use, to save metered proxy traffic; the data this "
              "scraper reads does not depend on them. Also "
              f"{LOAD_MEDIA_ENV}=1. No effect without a proxy or with "
              "--cdp-endpoint."))
    group.add_argument("--block-media", action="store_true", default=False,
                       help=argparse.SUPPRESS)


def _should_abort(resource_type: str) -> bool:
    return resource_type in BLOCKED_RESOURCE_TYPES


def install_playwright(context) -> None:
    """Abort image/media/font requests on a Playwright context."""
    def _route(route):
        if _should_abort(route.request.resource_type):
            route.abort()
        else:
            route.continue_()
    context.route("**/*", _route)
    logger.info("Blocking image/media/font requests to save proxy traffic "
                "(--load-media to load them).")


async def install_pyppeteer(page) -> None:
    """Abort image/media/font requests on a pyppeteer page.

    Not `page.setRequestInterception`: that sends `Network.
    setRequestInterception`, which current Chromium no longer has, and the
    call fails with "wasn't found". The Fetch domain is its replacement.
    It runs on a CDP session of its own, so this does not replace any Fetch
    handling the engine's page session already has (proxy auth), and its
    patterns pause ONLY the three blocked resource types: nothing else is
    held back waiting for a continue.
    """
    import asyncio
    session = await page.target.createCDPSession()

    def _on_paused(event):
        asyncio.ensure_future(session.send("Fetch.failRequest", {
            "requestId": event["requestId"], "errorReason": "BlockedByClient"}))
    session.on("Fetch.requestPaused", _on_paused)
    await session.send("Fetch.enable", {"patterns": [
        {"urlPattern": "*", "resourceType": rtype, "requestStage": "Request"}
        for rtype in CDP_BLOCKED_RESOURCE_TYPES]})
    logger.info("Blocking image/media/font requests to save proxy traffic "
                "(--load-media to load them).")


def install_selenium(driver) -> bool:
    """Block media URLs on a local chromedriver over CDP. False if refused.

    By file extension, not by resource type: Selenium has no request
    interception, so a media file served without an extension still loads.
    """
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Network.setBlockedURLs",
                               {"urls": list(BLOCKED_URL_PATTERNS)})
    except Exception as e:  # noqa: BLE001 — a saver must never break a run
        logger.warning("Could not block media over CDP (%s); loading it.",
                       type(e).__name__)
        return False
    logger.info("Blocking image/media/font URLs to save proxy traffic "
                "(--load-media to load them).")
    return True
