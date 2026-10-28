"""Pre-commit secret guard: block staged lines that look like API keys.

Usage: python scripts/check_secrets.py            (checks staged diff)
       python scripts/check_secrets.py FILE...    (checks given files)
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PATTERNS = [
    re.compile(r"LLM_[A-Za-z0-9_]{10,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
]


def find_hits(lines: list[tuple[str, int, str]]) -> list[str]:
    hits = []
    for path, lineno, text in lines:
        for pattern in PATTERNS:
            match = pattern.search(text)
            if match:
                masked = match.group(0)[:6] + "..."
                hits.append(f"{path}:{lineno}: matches {pattern.pattern!r} ({masked})")
    return hits


def staged_added_lines() -> list[tuple[str, int, str]]:
    diff = subprocess.run(
        ["git", "diff", "--cached", "--unified=0", "--no-color", "--diff-filter=ACMR"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout
    lines: list[tuple[str, int, str]] = []
    path, lineno = "?", 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else raw[4:]
        elif raw.startswith("@@"):
            match = re.search(r"\+(\d+)", raw)
            lineno = int(match.group(1)) if match else 0
        elif raw.startswith("+"):
            lines.append((path, lineno, raw[1:]))
            lineno += 1
    return lines


def file_lines(paths: list[str]) -> list[tuple[str, int, str]]:
    lines = []
    for p in paths:
        text = Path(p).read_text(encoding="utf-8", errors="replace")
        lines.extend((p, i, line) for i, line in enumerate(text.splitlines(), 1))
    return lines


def main(argv: list[str]) -> int:
    lines = file_lines(argv) if argv else staged_added_lines()
    hits = find_hits(lines)
    if hits:
        print("Secret check failed. Remove these before committing:", file=sys.stderr)
        for hit in hits:
            print("  " + hit, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
