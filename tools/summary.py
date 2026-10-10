#!/usr/bin/env python3
"""The submission-consistency summary for a pull request.

  summary.py render [--out FILE]   reads $NEEDS (toJSON(needs) from the workflow) and
                                   the reports in the checkout; writes Markdown; exit 1
                                   unless every required check succeeded.
  summary.py flags DIR             reads `be verify --json` outputs saved in DIR as
                                   <lab>__<run>.json; prints flags=<json> of their
                                   warnings, for $GITHUB_OUTPUT.

A consistent results summary starts with the fixed sentence in
_common.CONSISTENT_SENTENCE. Nothing here says a robot passed anything.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from collections.abc import Iterator

from _common import CONSISTENT_SENTENCE, ROLES, VERDICTS, ToolError, dig, read_json

JOBS = ("path-guard", "identity", "be-verify", "claim-lint")
CELL_RE = re.compile(r"[^A-Za-z0-9 ._:/+-]")
MAX_FLAGS_PER_RUN = 20

GREEN_MEANS = (
    "A green check means consistent, not passed: a FAIL report gets a green check when its "
    "data supports FAIL. Lab identity is self-asserted, thresholds are illustrative, and a "
    "result counts as reproduced only when a different lab reproduces it."
)


def cell(value: Any, limit: int = 80) -> str:
    """Plain, table-safe text from a value that came from a pull request."""
    text = CELL_RE.sub("?", str(value)) if value not in (None, "") else "-"
    return text if len(text) <= limit else text[: limit - 3] + "..."


def join_and(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def version_key(version: str) -> list[tuple[int, Any]]:
    """Natural order: 0.1.9 < 0.1.10; numbers sort before letters at the same place."""
    return [(0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"(\d+)", version) if p]


def run_info(repo: Path, run_dir: str) -> dict[str, str]:
    try:
        report = read_json(repo / run_dir / "report.json")
    except ToolError:
        report = {}
    verdict = dig(report, "verdict", "result")
    role = dig(report, "role")
    core = dig(report, "provenance", "core_version")
    return {
        "id": run_dir.removeprefix("results/"),
        "test": cell(dig(report, "test")),
        "role": role if role in ROLES else "?",
        "verdict": verdict if verdict in VERDICTS else "?",
        "core": cell(core, 40) if isinstance(core, str) else "",
        "reproduces": cell(dig(report, "reproduces")),
    }


def render(needs: dict[str, Any], repo: Path) -> tuple[bool, str]:
    guard = needs.get("path-guard") or {}
    outputs = guard.get("outputs") or {}
    kind = outputs.get("kind", "")
    lab = cell(outputs.get("lab", ""), 40)
    run_dirs = outputs.get("run_dirs", "").split()
    withdrawals = outputs.get("withdrawals", "").split()
    results = {job: (needs.get(job) or {}).get("result", "missing") for job in JOBS}

    if kind == "results" and run_dirs:
        required = JOBS
    elif kind in ("results", "lab"):
        required = ("path-guard", "identity")
    else:
        required = ("path-guard",)
    bad = [f"{job} ({result})" for job, result in results.items() if result in ("failure", "cancelled")]
    bad += [f"{job} (did not run)" for job in required if results[job] in ("skipped", "missing")]
    if bad or kind not in ("results", "lab"):
        if not bad:
            bad = ["path-guard (no kind reported)"]
        text = (
            "Not consistent. These checks did not succeed: " + join_and(bad) + ".\n\n"
            "See each job's log for the details. Nothing about any robot follows from this "
            "check either way.\n"
        )
        return False, text

    if kind == "lab":
        text = (
            f"Lab change for `{lab}`: lab.json is well formed, each key id matches its public key, "
            "no key id is used by another lab, and the pull request's author is listed. Lab identity "
            "is self-asserted. The maintainer reviews every lab change by hand.\n"
        )
        return True, text

    lines: list[str] = []
    if run_dirs:
        infos = [run_info(repo, d) for d in run_dirs]
        versions = sorted({i["core"] for i in infos if i["core"]}, key=version_key)
        lines.append(CONSISTENT_SENTENCE.format(core=join_and(versions) or "?"))
        lines += [
            "",
            "| Result | Test | Role | Verdict in the report | Core | Reproduces |",
            "|---|---|---|---|---|---|",
        ]
        for i in infos:
            lines.append(
                f"| `{i['id']}` | {i['test']} | {i['role']} | {i['verdict']} | {i['core'] or '-'} | {i['reproduces']} |"
            )
        lines += ["", GREEN_MEANS]
        flags = _parse_flags((needs.get("be-verify") or {}).get("outputs", {}).get("flags", ""))
        if flags:
            lines += ["", "Flags from `be verify` (these never block a submission):"]
            for result_id in sorted(flags):
                for flag in flags[result_id]:
                    lines.append(f"- `{cell(result_id)}`: {cell(flag.get('code'))}: {cell(flag.get('detail'), 200)}")
    if withdrawals:
        ids = ", ".join(f"`{d.removeprefix('results/')}`" for d in withdrawals)
        if lines:
            lines.append("")
        lines.append(
            f"Withdrawal notice for {ids}: WITHDRAWN.md is the only change, the run is already on main "
            "and stays there, and the pull request's author is listed for the lab."
        )
    return True, "\n".join(lines) + "\n"


def _parse_flags(raw: str) -> dict[str, list[dict[str, Any]]]:
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: [f for f in v if isinstance(f, dict)] for k, v in data.items() if isinstance(v, list)}


def _warnings(obj: Any) -> Iterator[dict[str, str]]:
    """Every finding-shaped object with severity "warn", wherever it sits in the output."""
    if isinstance(obj, dict):
        if obj.get("severity") == "warn" and isinstance(obj.get("code"), str):
            yield {"code": obj["code"], "detail": str(obj.get("detail", ""))[:200]}
        for value in obj.values():
            yield from _warnings(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _warnings(value)


def collect_flags(directory: Path) -> dict[str, list[dict[str, str]]]:
    flags: dict[str, list[dict[str, str]]] = {}
    if not directory.is_dir():
        return flags
    for path in sorted(directory.glob("*.json")):
        result_id = path.stem.replace("__", "/")
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        found = list(_warnings(data))[:MAX_FLAGS_PER_RUN]
        if found:
            flags[result_id] = found
    return flags


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rend = sub.add_parser("render", help="write the summary; exit 1 unless consistent")
    rend.add_argument("--repo", type=Path, default=Path("."), help="checkout of the pull request")
    rend.add_argument("--out", type=Path, help="append the summary here (e.g. $GITHUB_STEP_SUMMARY)")
    flg = sub.add_parser("flags", help="print flags=<json> from saved be verify outputs")
    flg.add_argument("directory", type=Path)
    args = parser.parse_args(argv)

    if args.command == "flags":
        compact = json.dumps(collect_flags(args.directory), sort_keys=True, separators=(",", ":"))
        print(f"flags={compact}")
        return 0

    try:
        needs = json.loads(os.environ.get("NEEDS", ""))
        if not isinstance(needs, dict):
            raise ValueError
    except ValueError:
        needs = {}
    ok, text = render(needs, args.repo)
    print(text, end="")
    if args.out:
        with open(args.out, "a", encoding="utf-8") as handle:
            handle.write(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
