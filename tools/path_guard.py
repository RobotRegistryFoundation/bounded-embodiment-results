#!/usr/bin/env python3
"""Path guard: decide what a pull request is from the files it changes.

A pull request may change files in exactly one of these places:

  results/<lab>/<run>/...  new runs for one lab, or a withdrawal notice
                           (WITHDRAWN.md added to a run already on main);
  labs/<lab>/...           one lab's registration or key change.

Anything else (.github/, tools/, kits.lock, POLICY.md, CODEOWNERS, READMEs,
licences) is a maintainer change. The guard rejects it and the maintainer
reviews it by hand.

In git mode (the default) it also refuses edits or deletions in a run already
on main, symbolic links, submodules, Git LFS pointers, private keys, files over
20 MB and runs over 50 MB.

  python3 tools/path_guard.py                        # git diff origin/main...HEAD
  python3 tools/path_guard.py --github-output "$GITHUB_OUTPUT"
  git diff --name-only origin/main...HEAD | python3 tools/path_guard.py --paths-from -

The second form lists paths only, so it checks their places but not statuses,
file types or sizes. CI uses git mode and runs this file from main, not from the
pull request. Exit status: 0 accepted, 1 rejected, 2 could not run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable

from _common import (
    DEFAULT_BASE,
    LAB_ID_RE,
    NAME_RE,
    RUN_DIR_RE,
    ToolError,
    git,
    git_ok,
    require_base,
    write_github_output,
)

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_RUN_BYTES = 50 * 1024 * 1024
MAX_WITHDRAWN_BYTES = 64 * 1024
WITHDRAWN = "WITHDRAWN.md"
LFS_POINTER = b"version https://git-lfs.github.com/spec/"
PEM_PRIVATE_KEY = re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
JWK_PRIVATE_D = re.compile(rb'"d"\s*:\s*"[A-Za-z0-9_-]{20,}"')
JWK_KTY = re.compile(rb'"kty"\s*:')
SCAN_BYTES = 1024 * 1024

RULE = (
    "Every changed path must be inside results/<one lab>/<run>/ (new runs, or "
    "WITHDRAWN.md for a run already on main) or inside labs/<one lab>/ "
    "(a lab registration or key change). See POLICY.md."
)


@dataclass(frozen=True)
class Change:
    path: str
    status: str = "?"  # git status letter: A, M, D, T; "?" when only the path is known
    mode: str = ""  # new file mode (old mode for a deletion); "" when unknown


@dataclass
class Verdict:
    kind: str = ""  # "results" or "lab"
    lab: str = ""
    lab_on_base: bool | None = None
    run_dirs: list[str] = field(default_factory=list)
    withdrawals: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def scope(self) -> str:
        if not self.ok:
            return ""
        return {"results": f"results/{self.lab}", "lab": f"labs/{self.lab}"}.get(self.kind, "")

    def describe(self) -> str:
        if not self.ok:
            lines = ["path-guard: rejected. " + RULE]
            lines += [f"  - {problem}" for problem in self.problems]
            return "\n".join(lines)
        if self.kind == "lab":
            what = {True: "change to a lab already on main", False: "new lab registration"}.get(
                self.lab_on_base, "lab registration or change"
            )
            return f'path-guard: accepted. Lab {what}: "{self.lab}" (labs/{self.lab}/ only).'
        lines = [
            f'path-guard: accepted. Results submission for lab "{self.lab}": '
            f"{len(self.run_dirs)} new run(s), {len(self.withdrawals)} withdrawal notice(s)."
        ]
        lines += [f"  new run:    {d}" for d in self.run_dirs]
        lines += [f"  withdrawal: {d}" for d in self.withdrawals]
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "kind": self.kind if self.ok else "",
            "lab": self.lab if self.ok else "",
            "scope": self.scope,
            "lab_on_base": self.lab_on_base,
            "run_dirs": self.run_dirs if self.ok else [],
            "withdrawals": self.withdrawals if self.ok else [],
            "problems": self.problems,
        }


def _shape_problem(path: str) -> str | None:
    if not path or path.startswith("/") or "\\" in path:
        return "not a relative path"
    if any(part in ("", ".", "..") for part in path.split("/")):
        return "not a normalized path"
    return None


def classify(
    changes: list[Change],
    run_on_base: Callable[[str], bool] | None = None,
    lab_on_base: Callable[[str], bool] | None = None,
) -> Verdict:
    """Classify changed paths. The callbacks say what is already on main (git mode)."""
    verdict = Verdict()
    if not changes:
        verdict.problems.append("the pull request changes no files")
        return verdict
    kinds: set[str] = set()
    labs: set[str] = set()
    runs: dict[str, list[Change]] = {}
    for change in changes:
        path = change.path
        shape = _shape_problem(path)
        if shape:
            verdict.problems.append(f"{path!r}: {shape}")
            continue
        if change.mode == "120000":
            verdict.problems.append(f"{path}: symbolic links are not accepted")
        elif change.mode == "160000":
            verdict.problems.append(f"{path}: submodules are not accepted")
        parts = path.split("/")
        top = parts[0]
        if top == "results":
            if len(parts) < 4:
                verdict.problems.append(f"{path}: result files must be inside results/<lab>/<run>/")
                continue
            lab, run = parts[1], parts[2]
            if not LAB_ID_RE.match(lab):
                verdict.problems.append(f"{path}: {lab!r} is not a valid lab id (see labs/README.md)")
                continue
            if not RUN_DIR_RE.match(run):
                verdict.problems.append(
                    f"{path}: {run!r} is not a run directory name (<test>-<UTC yyyymmddThhmmssZ>-s<seed>)"
                )
                continue
            bad = [p for p in parts[3:] if not NAME_RE.match(p)]
            if bad:
                verdict.problems.append(
                    f"{path}: names may use letters, digits, '.', '_' and '-' and must not start with '.'"
                )
                continue
            kinds.add("results")
            labs.add(lab)
            runs.setdefault(f"results/{lab}/{run}", []).append(change)
        elif top == "labs":
            if len(parts) < 3:
                verdict.problems.append(f"{path}: lab files must be inside labs/<lab>/")
                continue
            lab = parts[1]
            if not LAB_ID_RE.match(lab):
                verdict.problems.append(f"{path}: {lab!r} is not a valid lab id (see labs/README.md)")
                continue
            bad = [p for p in parts[2:] if not NAME_RE.match(p)]
            if bad:
                verdict.problems.append(
                    f"{path}: names may use letters, digits, '.', '_' and '-' and must not start with '.'"
                )
                continue
            kinds.add("lab")
            labs.add(lab)
        else:
            verdict.problems.append(f"{path}: outside results/ and labs/ (maintainer only; reviewed by hand)")
    if len(kinds) > 1:
        verdict.problems.append("a pull request is either a results submission or a lab change, not both")
    if len(labs) > 1:
        verdict.problems.append(f"the pull request touches more than one lab: {', '.join(sorted(labs))}")

    for run_dir, run_changes in sorted(runs.items()):
        names = {c.path[len(run_dir) + 1 :] for c in run_changes}
        on_base = run_on_base(run_dir) if run_on_base else None
        if on_base:
            if names == {WITHDRAWN} and all(c.status == "A" for c in run_changes):
                verdict.withdrawals.append(run_dir)
            else:
                verdict.problems.append(
                    f"{run_dir}: already on main; results are permanent, and the only change "
                    f"allowed is adding {WITHDRAWN}"
                )
        elif on_base is None and names == {WITHDRAWN}:
            verdict.withdrawals.append(run_dir)  # paths-only mode: can't tell; treat as a notice
        else:
            if WITHDRAWN in names:
                verdict.problems.append(f"{run_dir}: {WITHDRAWN} can only be added to a run already on main")
            odd = sorted(c.path for c in run_changes if c.status not in ("A", "?"))
            if odd:
                verdict.problems.append(f"{run_dir}: unexpected changes to {', '.join(odd)}")
            verdict.run_dirs.append(run_dir)

    if len(kinds) == 1 and len(labs) == 1:
        verdict.kind = kinds.pop()
        verdict.lab = labs.pop()
        if verdict.kind == "lab" and lab_on_base:
            verdict.lab_on_base = lab_on_base(verdict.lab)
    return verdict


def check_files(root: Path, changes: list[Change], verdict: Verdict) -> list[str]:
    """Content checks on the checked-out files (git mode only)."""
    problems: list[str] = []
    for change in changes:
        if change.status == "D" or _shape_problem(change.path):
            continue
        target = root / change.path
        try:
            info = os.lstat(target)
        except OSError:
            problems.append(f"{change.path}: changed in the pull request but missing from the checkout")
            continue
        if stat.S_ISLNK(info.st_mode):
            problems.append(f"{change.path}: symbolic links are not accepted")
            continue
        if not stat.S_ISREG(info.st_mode):
            problems.append(f"{change.path}: not a regular file")
            continue
        if info.st_size > MAX_FILE_BYTES:
            problems.append(f"{change.path}: {info.st_size} bytes; the limit is 20 MB per file")
        with open(target, "rb") as handle:
            head = handle.read(SCAN_BYTES)
        if head.startswith(LFS_POINTER):
            problems.append(f"{change.path}: a Git LFS pointer; LFS is not used here, commit the file itself")
        if PEM_PRIVATE_KEY.search(head) or (JWK_KTY.search(head) and JWK_PRIVATE_D.search(head)):
            problems.append(
                f"{change.path}: looks like a private key; remove it, and revoke the key if it was ever pushed"
            )
        if change.path.endswith("/" + WITHDRAWN):
            if info.st_size == 0:
                problems.append(f"{change.path}: empty; say when and why the result is withdrawn")
            elif info.st_size > MAX_WITHDRAWN_BYTES:
                problems.append(f"{change.path}: over 64 KB")
    for run_dir in verdict.run_dirs:
        total = 0
        for dirpath, _dirnames, filenames in os.walk(root / run_dir):
            for name in filenames:
                total += os.lstat(os.path.join(dirpath, name)).st_size
        if total > MAX_RUN_BYTES:
            problems.append(f"{run_dir}: {total} bytes in all; the limit is 50 MB per run")
    return problems


def changes_from_git(base: str, repo: Path) -> list[Change]:
    """Changed files on HEAD since it left `base`, without rename detection.

    --no-renames matters: with renames on, moving tools/x.py to results/... would
    list only the new path.
    """
    require_base(base, repo)
    raw = git(["diff", "--raw", "-z", "--no-renames", "--no-abbrev", f"{base}...HEAD"], repo)
    tokens = raw.split(b"\0")
    changes: list[Change] = []
    i = 0
    while i < len(tokens):
        meta = tokens[i]
        if not meta:
            i += 1
            continue
        if not meta.startswith(b":") or i + 1 >= len(tokens):
            raise ToolError("unexpected output from git diff --raw")
        fields = meta[1:].split()
        old_mode, new_mode, letter = fields[0].decode(), fields[1].decode(), fields[4][:1].decode()
        path = tokens[i + 1].decode("utf-8", "surrogateescape")
        changes.append(Change(path, letter, old_mode if letter == "D" else new_mode))
        i += 2
    return changes


def read_paths(source: str) -> list[str]:
    data = sys.stdin.buffer.read() if source == "-" else Path(source).read_bytes()
    separator = b"\0" if b"\0" in data else b"\n"
    return [p.decode("utf-8", "surrogateescape").strip() for p in data.split(separator) if p.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default=DEFAULT_BASE, help="base ref (default: origin/main)")
    parser.add_argument("--repo", default=".", help="checkout of the pull request (default: .)")
    parser.add_argument("--paths-from", metavar="FILE", help="read changed paths from FILE ('-' = stdin)")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--github-output", metavar="FILE", help="append kind, lab, scope, run_dirs, withdrawals")
    args = parser.parse_args(argv)
    repo = Path(args.repo)
    try:
        if args.paths_from:
            changes = [Change(p) for p in read_paths(args.paths_from)]
            verdict = classify(changes)
        else:
            changes = changes_from_git(args.base, repo)
            verdict = classify(
                changes,
                run_on_base=lambda d: git_ok(["cat-file", "-e", f"{args.base}:{d}"], repo),
                lab_on_base=lambda lab: git_ok(["cat-file", "-e", f"{args.base}:labs/{lab}/lab.json"], repo),
            )
            verdict.problems += check_files(repo, changes, verdict)
    except ToolError as exc:
        print(f"path-guard: could not run: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(verdict.as_dict(), indent=2) if args.json else verdict.describe())
    if args.github_output:
        data = verdict.as_dict()
        write_github_output(
            args.github_output,
            {
                "kind": data["kind"],
                "lab": data["lab"],
                "scope": data["scope"],
                "run_dirs": " ".join(data["run_dirs"]),
                "withdrawals": " ".join(data["withdrawals"]),
            },
        )
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    sys.exit(main())
