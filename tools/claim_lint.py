#!/usr/bin/env python3
"""Claim-language lint for result runs.

Flags the words in tools/claim-words.txt wherever they appear in a run's
report.json string values or in its NOTES.md, case-insensitively and as whole
words ("safe" is flagged; "safety" and "unsafe" are not). Object keys are not
scanned, and neither are signatures, key ids or hashes.

  python3 tools/claim_lint.py results/<lab>/<run> [...]

Prints one line per hit as file:field (report.json) or file:line (NOTES.md).
Exit status: 0 no hits, 1 hits or unreadable files.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any
from collections.abc import Iterator

from _common import ToolError, read_json

DEFAULT_WORDS = Path(__file__).resolve().parent / "claim-words.txt"
# Hyphen and dash look-alikes are folded to "-" so "BE‑1" (non-breaking hyphen) is caught.
DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−﹘﹣－"), "-")
# Never free text: cryptographic material and digests.
SKIP_KEYS = frozenset(
    {"signature", "kid", "sig", "sha256", "text_sha256", "stream_sha256", "wheel_sha256", "hash", "git_sha"}
)
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(DASHES)


def load_words(path: Path | str = DEFAULT_WORDS) -> list[str]:
    words = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            words.append(entry)
    if not words:
        raise ToolError(f"{path}: no claim words")
    return words


def compile_words(words: list[str]) -> list[tuple[str, re.Pattern[str]]]:
    return [(w, re.compile(r"(?<!\w)" + re.escape(normalize(w)) + r"(?!\w)", re.IGNORECASE)) for w in words]


def find_words(text: str, patterns: list[tuple[str, re.Pattern[str]]]) -> list[str]:
    folded = normalize(text)
    return [word for word, pattern in patterns if pattern.search(folded)]


def _join(where: str, key: str) -> str:
    if IDENT_RE.match(key):
        return f"{where}.{key}" if where else key
    return f"{where}[{key!r}]"


def iter_strings(obj: Any, where: str = "") -> Iterator[tuple[str, str]]:
    """(field path, value) for every string value, skipping SKIP_KEYS subtrees."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in SKIP_KEYS:
                continue
            yield from iter_strings(value, _join(where, key))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from iter_strings(value, f"{where}[{index}]")
    elif isinstance(obj, str):
        yield (where or "(top level)"), obj


def lint_run(run_dir: Path, patterns: list[tuple[str, re.Pattern[str]]]) -> tuple[list[str], list[str]]:
    """(hits, problems) for one run directory."""
    hits: list[str] = []
    problems: list[str] = []
    report = run_dir / "report.json"
    if report.is_file():
        try:
            doc = read_json(report)
        except ToolError as exc:
            problems.append(str(exc))
        else:
            for where, value in iter_strings(doc):
                for word in find_words(value, patterns):
                    hits.append(f'{report}:{where}: "{word}"')
    else:
        problems.append(f"{report}: missing")
    for notes in sorted(run_dir.rglob("NOTES.md")):
        if notes.is_symlink() or not notes.is_file():
            problems.append(f"{notes}: not a regular file")
            continue
        text = notes.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            for word in find_words(line, patterns):
                hits.append(f'{notes}:{number}: "{word}"')
    return hits, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("run_dirs", nargs="+", type=Path, help="results/<lab>/<run> directories")
    parser.add_argument("--words", type=Path, default=DEFAULT_WORDS, help="word list (default: tools/claim-words.txt)")
    args = parser.parse_args(argv)
    try:
        patterns = compile_words(load_words(args.words))
    except (OSError, ToolError) as exc:
        print(f"claim-lint: could not run: {exc}", file=sys.stderr)
        return 2
    all_hits: list[str] = []
    all_problems: list[str] = []
    for run_dir in args.run_dirs:
        hits, problems = lint_run(run_dir, patterns)
        all_hits += hits
        all_problems += problems
    for line in all_hits + all_problems:
        print(line)
    if all_hits or all_problems:
        print(
            f"claim-lint: {len(all_hits)} claim word(s) found, {len(all_problems)} unreadable file(s). "
            "Reword those fields and sign the report again (POLICY.md, Claim language)."
        )
        return 1
    print(f"claim-lint: no claim words in {len(args.run_dirs)} run(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
