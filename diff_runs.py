#!/usr/bin/env python3
"""diff_runs.py — diff two runs by `ticker`. No site knowledge.

Refuses to compare two runs that are not both `status == "complete"` in
their `.meta.json` sidecar: a partial run's un-fetched tickers would
otherwise read as delisted tickers, which is a false signal worse than no
diff at all.

Reports three independent kinds of change per ticker — `rating_changed`
(consensus Buy/Hold/Sell label), `price_target_changed` (average price
target), `smart_score_changed` — rather than one generic "changed", since
a ticker can change on one axis without the others.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Dict, List


def _load_meta(run_path: str) -> dict:
    meta_path = Path(f"{run_path}.meta.json")
    if not meta_path.exists():
        raise SystemExit(f"error: no sidecar found at {meta_path} — was this run produced by finish_run()?")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _load_rows(run_path: str) -> List[dict]:
    path = Path(run_path)
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    if path.suffix == ".csv":
        import csv
        with path.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    raise SystemExit(f"error: unsupported run file extension: {path.suffix}")


def _verify_output_hash(run_path: str, meta: dict) -> None:
    """Reject a new output paired with a stale sidecar after an interrupted write."""
    expected = meta.get("output_sha256")
    if not expected:  # Backward compatibility for runs created before v0.2.0.
        return
    path = Path(run_path)
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(
            f"error: output/sidecar checksum mismatch for {run_path}; "
            "the run may have been interrupted while saving"
        )


def _index_by_ticker(rows: List[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for row in rows:
        ticker = row.get("ticker")
        if ticker:
            out[ticker] = row
    return out


def _as_float(value) -> object:
    """CSV round-trips every field as a string; JSON keeps native types.
    Compare numerically either way so "334.9" == 334.9."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def diff(old_path: str, new_path: str) -> dict:
    old_meta, new_meta = _load_meta(old_path), _load_meta(new_path)
    _verify_output_hash(old_path, old_meta)
    _verify_output_hash(new_path, new_meta)
    for label, meta in (("old", old_meta), ("new", new_meta)):
        if meta.get("status") != "complete":
            raise SystemExit(
                f"error: refusing to diff — {label} run status is "
                f"{meta.get('status')!r}, not 'complete'. A partial run's "
                f"un-fetched tickers would read as delisted tickers."
            )

    old_rows = _index_by_ticker(_load_rows(old_path))
    new_rows = _index_by_ticker(_load_rows(new_path))
    old_tickers, new_tickers = set(old_rows), set(new_rows)

    added = sorted(new_tickers - old_tickers)
    removed = sorted(old_tickers - new_tickers)
    rating_changed, price_target_changed, smart_score_changed = [], [], []

    for ticker in sorted(old_tickers & new_tickers):
        o, n = old_rows[ticker], new_rows[ticker]

        if o.get("consensus_rating_id") != n.get("consensus_rating_id"):
            rating_changed.append({
                "ticker": ticker,
                "old_rating": o.get("consensus_rating_label"),
                "new_rating": n.get("consensus_rating_label"),
            })

        old_pt, new_pt = _as_float(o.get("price_target_average")), _as_float(n.get("price_target_average"))
        if old_pt != new_pt:
            price_target_changed.append({
                "ticker": ticker, "old_price_target": old_pt, "new_price_target": new_pt,
            })

        old_ss, new_ss = _as_float(o.get("smart_score")), _as_float(n.get("smart_score"))
        if old_ss != new_ss:
            smart_score_changed.append({
                "ticker": ticker, "old_smart_score": old_ss, "new_smart_score": new_ss,
            })

    return {
        "added": added, "removed": removed,
        "rating_changed": rating_changed,
        "price_target_changed": price_target_changed,
        "smart_score_changed": smart_score_changed,
        "old_count": len(old_rows), "new_count": len(new_rows),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Diff two tipranks-scraper runs by ticker")
    p.add_argument("old_run")
    p.add_argument("new_run")
    p.add_argument("--fail-on-change", action="store_true",
                    help="Exit 1 if any rating or price-target change is found")
    p.add_argument("--json", action="store_true", help="Print the diff as JSON instead of a summary")
    args = p.parse_args()

    result = diff(args.old_run, args.new_run)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{args.old_run} ({result['old_count']}) -> {args.new_run} ({result['new_count']})")
        print(f"  added:                {len(result['added'])}")
        print(f"  removed:              {len(result['removed'])}")
        print(f"  rating changed:       {len(result['rating_changed'])}")
        print(f"  price target changed: {len(result['price_target_changed'])}")
        print(f"  smart score changed:  {len(result['smart_score_changed'])}")

    if args.fail_on_change and (result["rating_changed"] or result["price_target_changed"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
