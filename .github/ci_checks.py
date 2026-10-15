#!/usr/bin/env python3
"""Repository-wide credential guard used by both CI and smoke_test.py.

Only paths and rule names are printed. A suspicious value is never echoed
back into a CI log, because an exception/report is a credential leak too.

This repo carries a real credential model since 2026-09-28: a 2Captcha API
key, TIPRANKS_PROXY and TIPRANKS_CDP_ENDPOINT, all read only through
env_config.py (CHANGELOG.md 0.2.0). Two false positives are handled below:
an unanchored openai_key pattern, and a Python type hint matching the
unquoted alternative of SECRET_ASSIGNMENT.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# Files exempt from SPECIFIC rules only, never wholesale: a real key pasted
# into one of them is still caught by every other rule.
RULE_EXEMPTIONS = {
    # The detector patterns themselves.
    ".github/ci_checks.py": {"credentialed_url", "secret_assignment"},
    # Documents placeholder connection strings on purpose.
    ".env.example": {"credentialed_url"},
    # Deliberate credential-shaped masking/redaction fixtures.
    "smoke_test.py": {"credentialed_url", "secret_assignment"},
}

TOKEN_RULES = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    "github_token": re.compile(r"(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
}
URL_CREDENTIALS = re.compile(r"\b(?:https?|wss?)://([^\s/@:]+):([^\s/@]+)@", re.I)
# A secret-shaped name, then `=` or `:`, then a quoted or unquoted value.
# Every quote may be preceded by a backslash: inside a JSON-string fixture
# the file holds `\"apiKey\": \"...\"`, and a bare-quote pattern matched
# nothing there.
SECRET_ASSIGNMENT = re.compile(
    r"\b(?:api[_-]?key|twocaptcha[_-]?key|TWOCAPTCHA_KEY|TIPRANKS_PROXY|"
    r"TIPRANKS_CDP_ENDPOINT|CLAUDE_CODE_OAUTH_TOKEN)"
    r"\\?['\"]?\s*(?:=|:)\s*"
    r"(?:\\?(['\"])([^'\"\\]{8,})\\?\1|([^\s#'\"\\]{8,}))",
    re.I,
)
# A 2Captcha key is 32 hex characters. Nothing in this repo needs a bare
# 32-hex string, so the shape is flagged wherever it appears, whatever it
# is assigned to.
# A bare lower-case identifier with no digit is a NAME, not a key:
# env_config's `"TWOCAPTCHA_KEY": "twocaptcha_key"` maps a variable to an
# argparse destination.
_IDENTIFIER_VALUE = re.compile(r"^[a-z_]+$")
TWOCAPTCHA_KEY_SHAPE = re.compile(r"(?<![0-9A-Fa-f])[0-9a-fA-F]{32}(?![0-9A-Fa-f])")
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

# Directories that are never this repo's own source: a node_modules tree, a
# cache, and ANY virtualenv. A virtualenv is recognised by the file that
# defines one, `pyvenv.cfg`, not by its name: a venv called `myenv/` was
# walked into, where pip's vendored code is full of credential-shaped
# strings. The name list stays as a cheap first filter. Paths are always
# relative to the repository, so a clone under /tmp, build/ or venv/
# scans the same set of files.
_NOT_SOURCE_DIR = re.compile(r"(^|/)(\.venv[^/]*|venv|env|node_modules|__pycache__|\.git)(/|$)")


def _inside_virtualenv(relative: str) -> bool:
    parts = Path(relative).parts[:-1]
    for i in range(1, len(parts) + 1):
        if (ROOT.joinpath(*parts[:i]) / "pyvenv.cfg").is_file():
            return True
    return False


def _git_paths(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", *args], cwd=ROOT, check=True, capture_output=True,
    )
    return [p.decode("utf-8", "surrogateescape") for p in result.stdout.split(b"\0") if p]


def repository_files() -> list[str]:
    """Tracked files, untracked non-ignored files, and IGNORED `.env.*`
    copies (`.env.bak`, `.env.local`, ...): those are ignored on purpose and
    are still where a live key sits one rename away from a commit. `.env`
    itself is the user's working configuration and is not scanned. Without
    a git working tree (a file-only copy or a release tarball) the scanner
    walks the filesystem instead."""
    try:
        paths = _git_paths("--cached", "--others", "--exclude-standard")
        backups = [
            p for p in _git_paths("--others", "--ignored", "--exclude-standard")
            if Path(p).name.startswith(".env.") and Path(p).name != ".env.example"
        ]
    except (subprocess.CalledProcessError, FileNotFoundError):
        paths = [
            Path(p.relative_to(ROOT)).as_posix()
            for p in ROOT.rglob("*")
            if p.is_file() and p.name != ".env"
        ]
        backups = []
    seen: set[str] = set()
    out: list[str] = []
    for p in paths + backups:
        if p in seen or _NOT_SOURCE_DIR.search(p) or _inside_virtualenv(p):
            continue
        seen.add(p)
        out.append(p)
    return out


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


_TYPE_HINT_RE = re.compile(
    r"^(?:Optional|Union|List|Dict|Tuple|Set|FrozenSet|Callable|Any|"
    r"str|int|float|bool|bytes|dict|list|tuple|set|None)\b"
)


def _looks_like_type_hint(value: str) -> bool:
    """True when an *unquoted* SECRET_ASSIGNMENT match is actually a
    Python type annotation, not a value. `api_key: Optional[str]` in a
    function signature (this repo's own scraper_api_client.py has one)
    matches the same `NAME\\s*(?::|=)\\s*<value>` shape a real
    `TIPRANKS_PROXY: some-value` config line would (the `:` case exists
    to catch YAML/env-style assignments), and Python parameter names
    routinely happen to be exactly the words this scanner watches for
    (`api_key`, ...). Checked ONLY for the unquoted alternative — a real
    secret is never legitimately written as an unquoted Python expression
    starting with a typing keyword."""
    return bool(_TYPE_HINT_RE.match(value))


def scan() -> tuple[list[tuple[str, int, str]], int]:
    """Returns (findings, number of files actually read)."""
    findings: list[tuple[str, int, str]] = []
    read = 0
    for relative in repository_files():
        exempt = RULE_EXEMPTIONS.get(relative, set())
        path = ROOT / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        read += 1
        for line_no, line in enumerate(text.splitlines(), 1):
            for rule, pattern in TOKEN_RULES.items():
                if rule not in exempt and pattern.search(line):
                    findings.append((relative, line_no, rule))
            if "credentialed_url" not in exempt:
                for match in URL_CREDENTIALS.finditer(line):
                    if not is_placeholder(match.group(1), match.group(2)):
                        findings.append((relative, line_no, "credentialed_url"))
            if "secret_assignment" not in exempt:
                for match in SECRET_ASSIGNMENT.finditer(line):
                    if match.group(3) and _looks_like_type_hint(match.group(3)):
                        continue
                    value = match.group(2) or match.group(3)
                    if _IDENTIFIER_VALUE.match(value):
                        continue
                    if not is_placeholder(value, value):
                        findings.append((relative, line_no, "secret_assignment"))
            if "twocaptcha_key_shape" not in exempt and TWOCAPTCHA_KEY_SHAPE.search(line):
                findings.append((relative, line_no, "twocaptcha_key_shape"))
    return findings, read


def main() -> int:
    findings, read = scan()
    if read == 0:
        # A scan that read nothing would otherwise print "passed".
        print("credential scan failed: no files were scanned", file=sys.stderr)
        return 1
    if findings:
        for path, line, rule in findings:
            print(f"{path}:{line}: possible committed credential ({rule})")
        print("credential scan failed", file=sys.stderr)
        return 1
    print(f"credential scan passed ({read} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
