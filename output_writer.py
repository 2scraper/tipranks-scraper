#!/usr/bin/env python3
"""output_writer.py — writer, exit codes, and run metadata for
tipranks-scraper. The exit-code contract and `finish_run()` outcome
precedence are the same shape as the rest of the 2scraper family
(CLAUDE.md §9), because that part is genuinely site-agnostic and a
caller integrating more than one family member benefits from one
consistent set of exit codes. The row schema (`TickerRating`, in
`tipranks_parser.py`) is NOT the family's `Product` dataclass — this
site has no price/brand/sku, and forcing that shape on analyst-consensus
data would misrepresent it. Here, "a unit of work" is one ticker, not one
listing page, so the precedence table is phrased in terms of tickers
requested/completed rather than pages.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
import time
from dataclasses import asdict, fields
from pathlib import Path
from typing import List, Optional, Sequence

from tipranks_parser import TickerRating

# --------------------------------------------------------------------------- #
# Exit codes — same values/meanings as every other 2scraper family member.
# --------------------------------------------------------------------------- #
EXIT_OK = 0
EXIT_CRASH = 1
EXIT_BAD_USAGE = 2
EXIT_BLOCKED = 3
EXIT_ZERO_PRODUCTS = 4
EXIT_REMOTE_API_ERROR = 5
EXIT_PARTIAL = 6

STATUS_BY_EXIT = {
    EXIT_OK: "complete",
    EXIT_CRASH: "crashed",
    EXIT_BAD_USAGE: "bad_usage",
    EXIT_BLOCKED: "blocked",
    EXIT_ZERO_PRODUCTS: "empty",
    EXIT_REMOTE_API_ERROR: "remote_api_error",
    EXIT_PARTIAL: "partial",
}

RATING_FIELD_NAMES: List[str] = [f.name for f in fields(TickerRating)]


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
def _atomic_write_text(path: str, text: str) -> None:
    """Durably replace one text file without exposing a truncated target."""
    target = Path(path)
    parent = target.parent if str(target.parent) else Path(".")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(parent))
    try:
        mode = (target.stat().st_mode & 0o777) if target.exists() else 0o644
        os.chmod(tmp_name, mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_json(ratings: Sequence[TickerRating], out_path: str) -> None:
    text = json.dumps([asdict(r) for r in ratings], ensure_ascii=False, indent=2)
    _atomic_write_text(out_path, text)


def write_csv(ratings: Sequence[TickerRating], out_path: str) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=RATING_FIELD_NAMES)
    writer.writeheader()  # written even for zero rows.
    for r in ratings:
        writer.writerow(asdict(r))
    _atomic_write_text(out_path, buffer.getvalue())


def write_output(ratings: Sequence[TickerRating], out_path: str, fmt: str) -> None:
    if fmt == "json":
        write_json(ratings, out_path)
    elif fmt == "csv":
        write_csv(ratings, out_path)
    else:
        raise ValueError(f"Unsupported format: {fmt!r} (expected 'json' or 'csv')")


# --------------------------------------------------------------------------- #
# Run metadata sidecar
# --------------------------------------------------------------------------- #
def meta_path_for(out_path: str) -> str:
    return f"{out_path}.meta.json"


def write_meta(
    out_path: str,
    *,
    status: str,
    stop_reason: str,
    engine: str,
    tickers_requested: List[str],
    tickers_completed: List[str],
    failed_tickers: Optional[List[str]] = None,
    rating_count: int,
    started_at: float,
    finished_at: Optional[float] = None,
    extra: Optional[dict] = None,
    output_sha256: Optional[str] = None,
) -> None:
    meta = {
        "status": status,
        "stop_reason": stop_reason,
        "engine": engine,
        "tickers_requested": tickers_requested,
        "tickers_completed": tickers_completed,
        "failed_tickers": failed_tickers or [],
        "rating_count": rating_count,
        "started_at": started_at,
        "finished_at": finished_at or time.time(),
    }
    if output_sha256:
        meta["output_sha256"] = output_sha256
    if extra:
        meta.update(extra)
    _atomic_write_text(
        meta_path_for(out_path), json.dumps(meta, ensure_ascii=False, indent=2)
    )


# --------------------------------------------------------------------------- #
# finish_run — decides exit code, whether to write output/sidecar at all.
# --------------------------------------------------------------------------- #
def finish_run(
    *,
    ratings: List[TickerRating],
    out_path: str,
    fmt: str,
    engine: str,
    tickers_requested: List[str],
    tickers_completed: List[str],
    failed_tickers: Optional[List[str]],
    blocked: bool,
    remote_api_error: bool,
    allow_empty: bool,
    started_at: float,
    extra_meta: Optional[dict] = None,
) -> int:
    """Same outcome precedence as the rest of the family (CLAUDE.md §9):
    remote_api_error > blocked > zero_products > partial > complete,
    decided once, independent of --allow-empty. A zero-result outcome
    writes neither file unless the caller explicitly passed --allow-empty;
    that flag is also the opt-in used by diagnostics/canaries that need a
    failed run's empty output and sidecar.

    `remote_api_error` is auto-derived (OR'd with whatever the caller
    passed) from `tickers_completed`/`failed_tickers`, the same way
    `partial` already was — a run where every single requested ticker
    failed and NONE completed (not even as a confirmed not-found) is a
    total remote/network failure, not an empty result, regardless of
    whether any engine bothered to compute that itself. Found live: all
    three engines were passing `remote_api_error=False` unconditionally,
    which made a total network outage indistinguishable from the site
    legitimately returning nothing (both landed on EXIT_ZERO_PRODUCTS) —
    fixed here, once, rather than in three call sites that can drift.

    That auto-derivation is deliberately suppressed when the caller also
    passed `blocked=True`: a total failure where every ticker was
    specifically blocked (a real 403/429, not a generic timeout) should
    surface as `blocked`, not `remote_api_error` — those mean different
    things to a caller (a WAF/rate-limit rejected you vs. the network or
    engine itself broke) and the precedence table above already ranks
    `remote_api_error` over `blocked` for when a caller has genuinely
    confirmed BOTH; treating every all-blocked run as remote_api_error
    instead would make `blocked` unreachable in the single most realistic
    case for it (a block usually takes out the whole run, not a cherry-
    picked subset of tickers)."""
    failed_tickers = failed_tickers or []
    partial = bool(failed_tickers) and bool(tickers_completed)
    zero_products = len(ratings) == 0
    total_failure = bool(failed_tickers) and not tickers_completed
    remote_api_error = remote_api_error or (total_failure and not blocked)

    if remote_api_error:
        status, exit_code = "remote_api_error", EXIT_REMOTE_API_ERROR
    elif blocked:
        status, exit_code = "blocked", EXIT_BLOCKED
    elif zero_products:
        status, exit_code = "empty", EXIT_ZERO_PRODUCTS
    elif partial:
        status, exit_code = "partial", EXIT_PARTIAL
    else:
        status, exit_code = "complete", EXIT_OK

    if zero_products and not allow_empty:
        return exit_code

    write_output(ratings, out_path, fmt)
    output_sha256 = hashlib.sha256(Path(out_path).read_bytes()).hexdigest()
    write_meta(
        out_path,
        status=status,
        stop_reason=status,
        engine=engine,
        tickers_requested=tickers_requested,
        tickers_completed=tickers_completed,
        failed_tickers=failed_tickers,
        rating_count=len(ratings),
        started_at=started_at,
        extra=extra_meta,
        output_sha256=output_sha256,
    )
    return exit_code
