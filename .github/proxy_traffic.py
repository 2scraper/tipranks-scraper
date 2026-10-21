"""Canary helper: put one run's proxy traffic in the step summary.

    python3 .github/proxy_traffic.py <label> <out-prefix>

Reads `<out>.latest_attempt.meta.json` (written on every metered run, rows
or not), else `<out>.meta.json`, and appends

    **<label>**: proxy traffic X.X MB of 10 MB

to $GITHUB_STEP_SUMMARY. A missing or unreadable sidecar, or a run that did
not go through a local proxy, is reported as such and is not a failure here
(the exit-code steps judge the run itself). A run that went over its proxy
budget prints `::error::stopped: proxy budget 10 MB exceeded` and exits 1.
"""

import json
import os
import sys

MB = 1_000_000


def _load(out):
    for suffix in (".latest_attempt.meta.json", ".meta.json"):
        try:
            with open(out + suffix, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return None


def _mb(n):
    value = n / MB
    return f"{value:g} MB" if value == int(value) else f"{value:.1f} MB"


def main(argv):
    if len(argv) != 3:
        print("usage: proxy_traffic.py <label> <out-prefix>")
        return 2
    label, out = argv[1], argv[2]
    meta = _load(out)
    exceeded = False
    if not isinstance(meta, dict):
        line = f"**{label}**: proxy traffic unknown (no sidecar was written)"
    elif "proxy_bytes" not in meta:
        line = (f"**{label}**: proxy traffic not metered (no local proxy "
                f"on this run)")
    else:
        used = (meta.get("proxy_bytes") or 0) / MB
        cap = meta.get("proxy_budget_bytes")
        cap_text = _mb(cap) if cap else "no budget"
        line = f"**{label}**: proxy traffic {used:.1f} MB of {cap_text}"
        exceeded = bool(meta.get("proxy_budget_exceeded"))
    print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(line + "\n\n")
    if exceeded:
        print(f"::error::stopped: proxy budget {cap_text} exceeded")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
