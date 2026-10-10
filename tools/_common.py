"""Shared constants and helpers for the results-repo tools.

Standard library only, Python 3.10+. Nothing in tools/ checks a report: that is
`be verify`, from the core wheel pinned in kits.lock. These tools decide which
files a pull request may touch, who may sign for a lab, which core to install,
and how to show the results.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

DEFAULT_BASE = "origin/main"
DRY_RUN_PREFIX = "test-"
MAINTAINER_LABEL = "maintainer's own"
VERDICTS = ("PASS", "FAIL", "INCONCLUSIVE")
ROLES = ("test", "control", "rehearsal")

# Lab ids: lower-case letters, digits and hyphens, 1-64 characters, starting and
# ending with a letter or digit (the "lab" pattern of the core's key-0.1 schema).
# "_template" never matches, so it is reserved.
LAB_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
# Run directories: <test>-<UTC yyyymmddThhmmssZ>-s<seed>, e.g. ev03-20261013T183000Z-s7
RUN_DIR_RE = re.compile(r"^(?P<test>[a-z0-9]+)-(?P<utc>\d{8}T\d{6}Z)-s(?P<seed>\d{1,20})$")
# One file or directory name inside a run or lab directory: no leading dot.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
GITHUB_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")

# The first sentence of every consistent results summary. Wording is fixed by
# POLICY.md; tests assert it verbatim.
CONSISTENT_SENTENCE = (
    "Consistent: signed by a registered key, files match, and the verdict follows "
    "from the submitted data under core {core}. CI cannot check that the data came "
    "from the robot described, or that the envelope is adequate."
)


class ToolError(Exception):
    """Input a tool cannot work with (bad JSON, missing git ref, unreadable file)."""


def loads_strict(data: bytes | str, what: str) -> Any:
    """Parse JSON, refusing duplicate keys and NaN/Infinity (core refuses them too)."""

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        obj: dict[str, Any] = {}
        for key, value in pairs:
            if key in obj:
                raise ToolError(f"{what}: duplicate key {key!r}")
            obj[key] = value
        return obj

    def bad_constant(name: str) -> Any:
        raise ToolError(f"{what}: {name} is not allowed in JSON")

    try:
        return json.loads(data, object_pairs_hook=pairs_hook, parse_constant=bad_constant)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ToolError(f"{what}: not valid JSON ({exc})") from None


def read_json(path: Path | str) -> Any:
    path = Path(path)
    if path.is_symlink():
        raise ToolError(f"{path}: is a symbolic link")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ToolError(f"{path}: cannot read ({exc.strerror})") from None
    return loads_strict(data, str(path))


def dig(obj: Any, *keys: str) -> Any:
    """obj[k1][k2]... or None when any step is missing or not an object."""
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def git(args: list[str], cwd: Path | str | None = None) -> bytes:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", "replace").strip()
        raise ToolError(f"git {' '.join(args)} failed: {message}")
    return proc.stdout


def git_ok(args: list[str], cwd: Path | str | None = None) -> bool:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False).returncode == 0


def require_base(base: str, cwd: Path | str | None = None) -> None:
    if not git_ok(["rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"], cwd):
        raise ToolError(f"base {base!r} not found; fetch it first (git fetch origin main)")


def git_show(base: str, path: str, cwd: Path | str | None = None) -> bytes | None:
    """The file at `path` on `base` (git show <base>:<path>), or None if it isn't there."""
    if not git_ok(["cat-file", "-e", f"{base}:{path}"], cwd):
        return None
    return git(["show", f"{base}:{path}"], cwd)


def write_github_output(path: str | Path, values: dict[str, str]) -> None:
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise ToolError(f"output {key!r} must be a single line")
            handle.write(f"{key}={value}\n")


def is_dry_run(lab: str) -> bool:
    return lab.startswith(DRY_RUN_PREFIX)
