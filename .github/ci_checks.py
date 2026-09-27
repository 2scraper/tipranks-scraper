#!/usr/bin/env python3
"""Repository-wide credential guard used by both CI and smoke_test.py.

Only paths and rule names are printed. A suspicious value is never echoed
back into a CI log, because an exception/report is a credential leak too.

This repo has no proxy/API-key/CDP credential model at all (see
SECURITY.md) — the ticker forecast page is free, public data. This
scanner is kept anyway, as defense-in-depth against something accidental
(a real token pasted into a commit message or a debug print), not because
this repo's own code carries a credential model to protect.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ALLOWLIST = {
    ".github/ci_checks.py",  # contains the detector patterns themselves
}

TOKEN_RULES = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    "github_token": re.compile(r"(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "openai_key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
}
URL_CREDENTIALS = re.compile(r"\b(?:https?|wss?)://([^\s/@:]+):([^\s/@]+)@", re.I)
SECRET_ASSIGNMENT = re.compile(
    r"\b(?:api[_-]?key|CLAUDE_CODE_OAUTH_TOKEN)\s*(?:=|:)\s*"
    r"(?:(['\"])([^'\"]{8,})\1|([^\s#'\"]{8,}))",
    re.I,
)
PLACEHOLDER_WORDS = (
    "user", "username", "login", "pass", "password", "example", "fake",
    "secret", "redacted", "changeme", "your", "test",
)
_WORD_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _words(token: str) -> set[str]:
    """Split into whole alphanumeric-run words on any other character
    (`-`, `_`, `:`, ...), lowercased — never substring containment (see
    `is_placeholder`'s docstring)."""
    return {w for w in _WORD_SPLIT_RE.split(token.lower()) if w}


_PLACEHOLDER_WORD_SET = {w for phrase in PLACEHOLDER_WORDS for w in _words(phrase)}

# A virtualenv (one per engine in CI — .venv-playwright/.venv-selenium/
# .venv-puppeteer, not just .venv) or a node_modules tree carries a huge
# amount of third-party test/fixture/license text that legitimately
# contains credential-shaped strings. `.gitignore` is supposed to keep
# these out of `git ls-files --others --exclude-standard` already, but
# relying on that alone is exactly the "two independent checks for the
# same thing WILL drift apart" trap — filtering by name here too means a
# stale or incomplete .gitignore degrades to redundant, not broken.
_NOT_SOURCE_DIR = re.compile(r"(^|/)(\.venv[^/]*|venv|env|node_modules|__pycache__|\.git)(/|$)")


def repository_files() -> list[str]:
    """`git ls-files` when ROOT is a real git working tree (true in CI's
    `actions/checkout`, and in any real clone) — a plain filesystem walk
    as a fallback otherwise (a file-only sync of this repo with no .git
    directory, or an extracted release tarball), so the scanner works
    either way rather than crashing when .git is absent."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, check=True, capture_output=True,
        )
        paths = [p.decode("utf-8", "surrogateescape") for p in result.stdout.split(b"\0") if p]
    except (subprocess.CalledProcessError, FileNotFoundError):
        paths = [
            str(p.relative_to(ROOT))
            for p in ROOT.rglob("*")
            if p.is_file()
        ]
    return [p for p in paths if not _NOT_SOURCE_DIR.search(p)]


def is_placeholder(user: str, password: str) -> bool:
    """True when `user`/`password` look like a documentation placeholder
    rather than a real credential. A whole-word check, not substring
    containment — `myrealuser1234:myrealpass5678` must NOT match on
    "user"/"pass" as a substring, or a real credential silently never
    gets scanned."""
    combined = f"{user}{password}"
    if "{" in combined or "}" in combined:
        return True
    return bool((_words(user) | _words(password)) & _PLACEHOLDER_WORD_SET)


def scan() -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    for relative in repository_files():
        if relative in FIXTURE_ALLOWLIST:
            continue
        path = ROOT / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            for rule, pattern in TOKEN_RULES.items():
                if pattern.search(line):
                    findings.append((relative, line_no, rule))
            for match in URL_CREDENTIALS.finditer(line):
                if not is_placeholder(match.group(1), match.group(2)):
                    findings.append((relative, line_no, "credentialed_url"))
            for match in SECRET_ASSIGNMENT.finditer(line):
                value = match.group(2) or match.group(3)
                if not is_placeholder(value, value):
                    findings.append((relative, line_no, "secret_assignment"))
    return findings


def main() -> int:
    findings = scan()
    if findings:
        for path, line, rule in findings:
            print(f"{path}:{line}: possible committed credential ({rule})")
        print("credential scan failed", file=sys.stderr)
        return 1
    print("credential scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
