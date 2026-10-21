"""
proxy_meter.py
---------------
Count, and cap, the bytes a run sends through a local proxy.

A residential proxy is billed by the byte, and until this existed nothing in
a run said how many bytes it had used: a slow page, a retry loop or a feed
that kept scrolling could spend a proxy balance and the only record was the
proxy provider's invoice. So every run that goes out through a LOCAL proxy
(`--proxy` / `--proxy-file` / `TIPRANKS_PROXY`) now:

* **meters** the traffic per run, in every engine:

  - Playwright: `request.sizes()` of every finished or failed request —
    request headers + request body + response headers + response body;
  - pyppeteer: Chrome DevTools Protocol `Network.loadingFinished.
    encodedDataLength` (bytes received, headers included) plus an estimate
    of each request's own line, headers and body from
    `Network.requestWillBeSent`;
  - Selenium: the same CDP events, read from chromedriver's performance log
    after each page;
  - an HTTP client: the response's headers + body as received, plus the
    request line, headers and body it sent;

* **caps** it: `--proxy-budget-mb` (default 10 MB, `0` = no cap;
  also `TIPRANKS_PROXY_BUDGET_MB`). Once the running total is over the
  budget, every further request is refused (Playwright aborts it in a route,
  pyppeteer and Selenium block every URL over CDP) and the engine stops at
  its next navigation. The rows already gathered are kept and written; the
  run ends with `stop_reason: proxy_budget_exceeded` and exits 6 (partial)
  when it holds rows, 5 (content not obtained) when it holds none. It never
  continues quietly past the cap;

* **records** it in the run sidecar (`<out>.meta.json`, and
  `<out>.latest_attempt.meta.json`, which is written on every metered run
  so a run that wrote no rows still says what it spent):
  `proxy_bytes`, `proxy_budget_bytes` (null when there is no cap) and
  `proxy_budget_exceeded`, and logs `Proxy traffic: X.X MB of Y MB budget`
  at the end.

What the figure is: an ESTIMATE of the payload that went through the
tunnel, as the browser (or the HTTP client) accounts for it. TLS record
framing, handshakes and the CONNECT exchange with the proxy are not visible
to any of them and are not counted; they add a few percent on top. The
proxy pre-check (one CONNECT and a HEAD, under 1 KB) is not counted either.
Over `--cdp-endpoint` (the 2Captcha Scraping Browser API, billed on its own
terms) and without a proxy nothing is metered and none of these fields is
written. A megabyte here is 1,000,000 bytes.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("proxy_meter")

BUDGET_ENV = "TIPRANKS_PROXY_BUDGET_MB"
DEFAULT_BUDGET_MB = 10.0
MB = 1_000_000
STOP_BUDGET_EXCEEDED = "proxy_budget_exceeded"

_ARGS_ATTR = "_proxy_meter"


class ProxyBudgetExceeded(BaseException):
    """Raised at the next navigation once the proxy budget is spent.

    A BaseException, not an Exception, on purpose: the engines' retry loops
    catch `Exception` broadly so that one bad page cannot crash a run, and a
    budget stop swallowed by one of them would be retried — spending more of
    the very traffic it exists to protect. Like KeyboardInterrupt it passes
    every `except Exception` and is caught only where the run is finished.
    """


class ProxyMeter:
    """A running byte count with an optional cap. Thread-safe enough: one
    integer add per event, and the engines here run one worker per meter."""

    def __init__(self, budget_mb: float = DEFAULT_BUDGET_MB):
        self.budget_bytes: Optional[int] = (
            int(round(budget_mb * MB)) if budget_mb and budget_mb > 0 else None)
        self.bytes = 0
        self.requests = 0
        self._tripped = False
        self._on_exceed: List[Callable[[], Any]] = []

    @property
    def exceeded(self) -> bool:
        return self.budget_bytes is not None and self.bytes > self.budget_bytes

    def on_exceed(self, callback: Callable[[], Any]) -> None:
        """Run `callback` once when the total first goes over the budget
        (immediately, if it already has)."""
        self._on_exceed.append(callback)
        if self._tripped:
            _quietly(callback)

    def add(self, n: int) -> bool:
        """Count `n` bytes; return True while the run is over budget."""
        if n and n > 0:
            self.bytes += int(n)
        self.requests += 1
        if self.exceeded and not self._tripped:
            self._tripped = True
            logger.error("Proxy budget exceeded: %s used of %s. Refusing every "
                         "further request; the rows gathered so far are kept.",
                         fmt_mb(self.bytes), fmt_mb(self.budget_bytes))
            for callback in list(self._on_exceed):
                _quietly(callback)
        return self.exceeded

    def check(self) -> None:
        if self.exceeded:
            raise ProxyBudgetExceeded(
                f"proxy budget {fmt_mb(self.budget_bytes)} exceeded "
                f"({fmt_mb(self.bytes)} used)")

    def sidecar(self) -> Dict[str, Any]:
        return {"proxy_bytes": self.bytes,
                "proxy_budget_bytes": self.budget_bytes,
                "proxy_budget_exceeded": self.exceeded}

    def summary(self) -> str:
        cap = (f"{fmt_mb(self.budget_bytes)} budget" if self.budget_bytes
               else "no budget (--proxy-budget-mb 0)")
        return f"Proxy traffic: {fmt_mb(self.bytes)} of {cap}"


def _quietly(callback) -> None:
    try:
        callback()
    except Exception as e:  # noqa: BLE001 — enforcement is also done at the next navigation
        logger.warning("Could not refuse further proxy requests (%s); the run "
                       "stops at its next navigation instead.", type(e).__name__)


def fmt_mb(n: Optional[int]) -> str:
    return "unlimited" if n is None else f"{n / MB:.1f} MB"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _budget_arg(text: str) -> float:
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"not a number: {text!r}")
    if value < 0 or value != value or value == float("inf"):
        raise argparse.ArgumentTypeError(
            f"must be 0 (no cap) or a positive number of MB, got {text!r}")
    return value


def add_budget_flag(parser) -> None:
    parser.add_argument(
        "--proxy-budget-mb", type=_budget_arg, default=None, metavar="MB",
        help=(f"Stop the run once this many megabytes (10^6 bytes) have gone "
              f"through a local --proxy/--proxy-file (default "
              f"{DEFAULT_BUDGET_MB:g}; 0 = no cap; also {BUDGET_ENV}). The "
              f"rows gathered so far are kept and the run exits 6 (5 if it "
              f"holds none) with stop_reason proxy_budget_exceeded. The count "
              f"is the browser's (or HTTP client's) own byte accounting of "
              f"the traffic it sent and received; TLS and CONNECT overhead "
              f"(a few percent) is not included. Not used with "
              f"--cdp-endpoint or without a proxy."))


def budget_mb(args) -> float:
    """The budget for this run: the flag, else the environment, else 10.

    Exits 2 (bad usage) on an unparseable environment value rather than
    falling back to a default: a cap the caller believes is set and is not
    is the failure this exists to prevent.
    """
    value = getattr(args, "proxy_budget_mb", None)
    if value is None or value == "":
        raw = os.environ.get(BUDGET_ENV, "").strip()
        if not raw:
            return DEFAULT_BUDGET_MB
        try:
            return _budget_arg(raw)
        except argparse.ArgumentTypeError as e:
            raise SystemExit(f"error: {BUDGET_ENV}: {e}") from None
    if isinstance(value, str):
        try:
            return _budget_arg(value)
        except argparse.ArgumentTypeError as e:
            raise SystemExit(f"error: {BUDGET_ENV}: {e}") from None
    return float(value)


def start(args, pool, remote: bool = False) -> Optional[ProxyMeter]:
    """Create this run's meter (None without a local proxy) and pin it to
    `args`, so every helper that already receives `args` can reach it."""
    meter = ProxyMeter(budget_mb(args)) if (pool and not remote) else None
    try:
        setattr(args, _ARGS_ATTR, meter)
    except AttributeError:
        pass
    if meter is not None:
        logger.info("Metering proxy traffic (budget %s).",
                    fmt_mb(meter.budget_bytes))
    return meter


def of(args) -> Optional[ProxyMeter]:
    return getattr(args, _ARGS_ATTR, None)


def check(args) -> None:
    """Raise ProxyBudgetExceeded if this run's budget is spent. Call it
    before every navigation, scroll and retry."""
    meter = of(args)
    if meter is not None:
        meter.check()


def finish(args, extra: Optional[dict] = None) -> Optional[dict]:
    """Merge the meter's fields into `extra`, log the total; return extra."""
    meter = of(args)
    if meter is None:
        return extra
    extra = dict(extra or {})
    extra.update(meter.sidecar())
    log = logger.warning if meter.exceeded else logger.info
    log("%s", meter.summary())
    return extra


# ---------------------------------------------------------------------------
# Playwright
# ---------------------------------------------------------------------------

def _playwright_size(request) -> int:
    try:
        sizes = request.sizes()
    except Exception:  # noqa: BLE001 — a request that never got a response
        return 0
    return sum(max(0, int(sizes.get(k) or 0)) for k in (
        "requestHeadersSize", "requestBodySize",
        "responseHeadersSize", "responseBodySize"))


def install_playwright(context, meter: Optional[ProxyMeter]) -> None:
    """Meter every request on `context`, and abort new ones once over budget.

    Install it AFTER any other route on the context: Playwright runs routes
    in reverse order of registration, so this one decides first and hands
    everything it lets through to the next with `route.fallback()`.
    """
    if meter is None:
        return

    def _count(request):
        meter.add(_playwright_size(request))

    context.on("requestfinished", _count)
    context.on("requestfailed", _count)

    def _gate(route):
        if meter.exceeded:
            route.abort("blockedbyclient")
        else:
            route.fallback()
    context.route("**/*", _gate)


# ---------------------------------------------------------------------------
# CDP (pyppeteer, Selenium)
# ---------------------------------------------------------------------------

def _request_estimate(request: dict) -> int:
    """Bytes of an HTTP/1.1 request line + headers + body, from CDP's view."""
    line = f"{request.get('method', 'GET')} {request.get('url', '')} HTTP/1.1\r\n"
    headers = request.get("headers") or {}
    size = len(line.encode("utf-8", "replace")) + 2
    for k, v in headers.items():
        size += len(f"{k}: {v}\r\n".encode("utf-8", "replace"))
    body = request.get("postData")
    if body:
        size += len(body.encode("utf-8", "replace"))
    return size


class CdpCounter:
    """Turns CDP Network events into bytes on a meter.

    Only what crossed the network is counted:

    * a request is counted only for an `http://` / `https://` URL. The
      browser's own loads (`chrome://` new-tab page scripts, `data:`,
      `blob:`, extensions) never reach the proxy; measured on a sibling
      repo's Selenium run, the new-tab page alone was ~2.8 MB of
      `loadingFinished` events while the proxy carried ~6 KB;
    * response bytes are counted only after a `responseReceived` that came
      over the network: not a cache hit (`fromDiskCache`,
      `fromPrefetchCache`, `fromServiceWorker`, or a
      `requestServedFromCache` event, which also cancels the request's
      own estimate), and not Chrome's own error page, which is reported as
      a finished load of the failed URL with no `responseReceived`.

    `loadingFinished` then adds the request estimate plus
    `encodedDataLength` (every byte received, headers included);
    `loadingFailed` adds the request estimate plus what `dataReceived` had
    delivered, unless the browser blocked the request itself (media
    blocking, the budget's own block). A redirect hop adds its own
    request and its response's `encodedDataLength`.
    """

    def __init__(self, meter: ProxyMeter):
        self.meter = meter
        self._req: Dict[str, int] = {}
        self._recv: Dict[str, int] = {}
        self._responded: Dict[str, bool] = {}

    @staticmethod
    def _network(url) -> bool:
        return str(url or "").startswith(("http://", "https://"))

    def feed(self, method: str, params: dict) -> None:
        if not isinstance(params, dict):
            return
        rid = params.get("requestId")
        if method == "Network.requestWillBeSent":
            redirect = params.get("redirectResponse")
            if redirect and rid in self._req:
                self.meter.add(self._req.pop(rid, 0)
                               + int(redirect.get("encodedDataLength") or 0))
            self._recv.pop(rid, None)
            self._responded.pop(rid, None)
            request = params.get("request") or {}
            if self._network(request.get("url")):
                self._req[rid] = _request_estimate(request)
            else:
                self._req.pop(rid, None)
        elif method == "Network.requestServedFromCache":
            self._req.pop(rid, None)
            self._responded[rid] = False
        elif method == "Network.responseReceived":
            response = params.get("response") or {}
            cached = (response.get("fromDiskCache")
                      or response.get("fromPrefetchCache")
                      or response.get("fromServiceWorker"))
            if cached:
                self._req.pop(rid, None)
            if self._responded.get(rid) is not False:
                self._responded[rid] = bool(
                    self._network(response.get("url")) and not cached)
        elif method == "Network.dataReceived":
            if self._responded.get(rid):
                self._recv[rid] = (self._recv.get(rid, 0)
                                   + int(params.get("encodedDataLength") or 0))
        elif method == "Network.loadingFinished":
            got = (int(params.get("encodedDataLength") or 0)
                   if self._responded.pop(rid, False) else 0)
            sent = self._req.pop(rid, 0)
            self.meter.add(sent + max(got, self._recv.pop(rid, 0)))
        elif method == "Network.loadingFailed":
            sent = self._req.pop(rid, 0)
            got = self._recv.pop(rid, 0)
            self._responded.pop(rid, None)
            if params.get("blockedReason") or "BLOCKED_BY_CLIENT" in str(
                    params.get("errorText", "")):
                return
            self.meter.add(sent + got)


EVENTS = ("Network.requestWillBeSent", "Network.requestServedFromCache",
          "Network.responseReceived", "Network.dataReceived",
          "Network.loadingFinished", "Network.loadingFailed")


async def install_pyppeteer(page, meter: Optional[ProxyMeter]) -> None:
    """Meter `page` over a CDP session of its own; block every URL once
    over budget."""
    if meter is None:
        return
    import asyncio
    session = await page.target.createCDPSession()
    counter = CdpCounter(meter)
    for name in EVENTS:
        session.on(name, (lambda n: lambda params: counter.feed(n, params))(name))
    await session.send("Network.enable", {})

    # The budget can trip on another thread (a concurrent worker, or a
    # thread that drains another engine's events) that has no event loop of
    # its own: hand the block to the loop this page runs on.
    loop = asyncio.get_event_loop()

    async def _send_block():
        try:
            await session.send("Network.setBlockedURLs", {"urls": ["*"]})
        except Exception:  # noqa: BLE001 — the next navigation's check still stops the run
            pass

    def _block():
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_send_block()))
    meter.on_exceed(_block)


SELENIUM_LOGGING_PREFS = {"performance": "ALL"}


def selenium_options(options, meter: Optional[ProxyMeter]) -> None:
    """Ask chromedriver for the performance log the meter reads."""
    if meter is None:
        return
    options.set_capability("goog:loggingPrefs", SELENIUM_LOGGING_PREFS)


def install_selenium(driver, meter: Optional[ProxyMeter]) -> None:
    if meter is None:
        return
    counter = CdpCounter(meter)
    setattr(driver, "_proxy_meter_counter", counter)

    def _block():
        driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": ["*"]})
    meter.on_exceed(_block)


def selenium_log(driver) -> list:
    """`driver.get_log("performance")`, with the meter fed on the way.

    Chromedriver's log is drained on read, so an engine that reads it for
    its own purposes must read it through here, or the meter misses every
    event that read consumed. Returns the raw entries.
    """
    try:
        entries = driver.get_log("performance")
    except Exception as e:  # noqa: BLE001 — an absent log must never break a run
        logger.debug("No performance log (%s)", type(e).__name__)
        return []
    counter = getattr(driver, "_proxy_meter_counter", None)
    if counter is not None:
        for entry in entries:
            try:
                message = json.loads(entry["message"])["message"]
            except (KeyError, TypeError, ValueError):
                continue
            method = message.get("method", "")
            if method in EVENTS:
                counter.feed(method, message.get("params") or {})
    return entries


def drain_selenium(driver) -> None:
    """Read chromedriver's performance log into the meter. Call it after
    every page load and scroll, and once before the driver quits."""
    if driver is not None and getattr(driver, "_proxy_meter_counter", None):
        selenium_log(driver)


# ---------------------------------------------------------------------------
# HTTP clients (requests)
# ---------------------------------------------------------------------------

def count_response(meter: Optional[ProxyMeter], response) -> None:
    """Count one `requests` response: what was sent and what came back."""
    if meter is None or response is None:
        return
    size = 0
    req = getattr(response, "request", None)
    if req is not None:
        size += len(f"{req.method} {req.url} HTTP/1.1\r\n".encode()) + 2
        size += sum(len(f"{k}: {v}\r\n".encode("utf-8", "replace"))
                    for k, v in (req.headers or {}).items())
        body = req.body
        if body:
            size += len(body if isinstance(body, bytes) else
                        str(body).encode("utf-8", "replace"))
    size += len(f"HTTP/1.1 {response.status_code} {response.reason}\r\n".encode(
        "utf-8", "replace")) + 2
    size += sum(len(f"{k}: {v}\r\n".encode("utf-8", "replace"))
                for k, v in (response.headers or {}).items())
    # The body as it crossed the wire: Content-Length when the server sent
    # one (it is the compressed size), otherwise the decoded body's length.
    wire = None
    try:
        wire = int(response.headers.get("Content-Length"))
    except (TypeError, ValueError, AttributeError):
        pass
    size += wire if wire is not None else len(response.content or b"")
    meter.add(size)
