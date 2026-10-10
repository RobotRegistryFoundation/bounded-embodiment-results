#!/usr/bin/env python3
"""Pin the core: write a pip requirements file for one released core version.

The version comes from --version, or from a run's report.json
(provenance.core_version). It must be listed in kits.lock, which maps each
released version of `bounded-embodiment` to the sha256 of its wheel:

  {"bounded-embodiment": {"0.1.0": {"wheel_sha256": "<64 hex>", "released": "YYYY-MM-DD"}}}

A version that isn't listed (unreleased, development or unknown) is refused.

  python3 tools/pin_core.py --run-dir results/<lab>/<run> --out core-requirements.txt
  pip install --no-deps --only-binary :all: --require-hashes -r core-requirements.txt

kits.lock pins the core wheel only. pip's --require-hashes mode needs a hash for
every package it installs, so CI installs the core with --no-deps first, then
its dependencies (jsonschema, cryptography) from PyPI by the core's own ranges.

Prints the version on stdout. Exit status: 0 pinned, 1 refused, 2 could not run.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from _common import ToolError, dig, read_json

PACKAGE = "bounded-embodiment"
DEFAULT_LOCK = Path(__file__).resolve().parent.parent / "kits.lock"
# Releases only: X.Y.Z, optionally aN/bN/rcN and .postN. No .devN, no +local.
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?(?:\.post\d+)?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Refused(Exception):
    """The version can't be used."""


def load_lock(path: Path | str = DEFAULT_LOCK) -> dict[str, dict[str, str]]:
    doc = read_json(path)
    if not isinstance(doc, dict) or set(doc) != {PACKAGE}:
        raise ToolError(f'{path}: must be an object with the single key "{PACKAGE}"')
    entries = doc[PACKAGE]
    if not isinstance(entries, dict):
        raise ToolError(f'{path}: "{PACKAGE}" must map versions to entries')
    for version, entry in entries.items():
        if not VERSION_RE.match(version):
            raise ToolError(f"{path}: {version!r} is not a released version number")
        if not isinstance(entry, dict) or set(entry) != {"wheel_sha256", "released"}:
            raise ToolError(f'{path}: {version}: entry must have exactly "wheel_sha256" and "released"')
        if not isinstance(entry["wheel_sha256"], str) or not SHA256_RE.match(entry["wheel_sha256"]):
            raise ToolError(f"{path}: {version}: wheel_sha256 must be 64 lower-case hex digits")
        if not isinstance(entry["released"], str) or not DATE_RE.match(entry["released"]):
            raise ToolError(f"{path}: {version}: released must be a date, YYYY-MM-DD")
    return entries


def requirement_line(entries: dict[str, dict[str, str]], version: Any) -> str:
    if not isinstance(version, str) or not VERSION_RE.match(version):
        raise Refused(f"core version {version!r} is not a release; development and local builds are refused")
    entry = entries.get(version)
    if entry is None:
        raise Refused(f"core {version} is not in kits.lock; only released versions listed there can be used")
    return f"{PACKAGE}=={version} --hash=sha256:{entry['wheel_sha256']}\n"


def core_version_of(run_dir: Path | str) -> Any:
    report = read_json(Path(run_dir) / "report.json")
    version = dig(report, "provenance", "core_version")
    if version is None:
        raise Refused(f"{run_dir}/report.json has no provenance.core_version")
    return version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--version", help="core version, e.g. 0.1.0")
    source.add_argument("--run-dir", type=Path, help="read provenance.core_version from this run's report.json")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK, help="kits.lock (default: the repo's)")
    parser.add_argument("--out", type=Path, help="write the requirements file here")
    args = parser.parse_args(argv)
    try:
        entries = load_lock(args.lock)
        version = args.version if args.version is not None else core_version_of(args.run_dir)
        line = requirement_line(entries, version)
    except Refused as exc:
        print(f"pin-core: refused: {exc}", file=sys.stderr)
        return 1
    except ToolError as exc:
        print(f"pin-core: could not run: {exc}", file=sys.stderr)
        return 2
    if args.out:
        args.out.write_text(line, encoding="utf-8")
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
