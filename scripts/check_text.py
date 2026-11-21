"""Fail if any tracked (or staged) text file contains an em dash or an en dash.

House style: use a comma, colon, parentheses or a plain hyphen instead.
Run from the repository root: python scripts/check_text.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

FORBIDDEN = {chr(0x2014): "em dash", chr(0x2013): "en dash"}
TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".json", ".md",
    ".txt", ".toml", ".yml", ".yaml", ".ini", ".cfg", ".sh", ".example",
}
SKIP = {"docs/Proposal.docx", "docs/Proposal.pdf"}


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached"],
        check=True, capture_output=True, text=True,
    ).stdout
    files = []
    for line in out.splitlines():
        if line in SKIP:
            continue
        path = Path(line)
        if path.suffix in TEXT_SUFFIXES or path.name in {".gitignore", ".env.example"}:
            files.append(path)
    return files


def main() -> int:
    problems = []
    for path in tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (FileNotFoundError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for char, name in FORBIDDEN.items():
                if char in line:
                    problems.append(f"{path}:{lineno}: {name}")
    if problems:
        print("Forbidden dashes found:")
        print("\n".join(problems))
        return 1
    print("Text check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
