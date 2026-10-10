#!/usr/bin/env python3
"""Build the results table from main: site/index.html and site/results.json.

Reads results/<lab>/<run>/report.json, labs/<lab>/lab.json and
tools/maintainer.json. It checks nothing again: each row's pull request was
checked for consistency before it was merged. Dry-run labs (test-*) are left
out. There is no level column, and an envelope's self-declared level is never
read.

  python3 tools/build_table.py [--repo .] [--out site]

Links point at $GITHUB_REPOSITORY (default RobotRegistryFoundation/
bounded-embodiment-results); $GITHUB_SHA, when set, is recorded as the source.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from _common import (
    LAB_ID_RE,
    MAINTAINER_LABEL,
    ROLES,
    RUN_DIR_RE,
    VERDICTS,
    ToolError,
    dig,
    is_dry_run,
    read_json,
)

TITLE = "Bounded Embodiment results"
DEFAULT_REPOSITORY = "RobotRegistryFoundation/bounded-embodiment-results"
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_MAINTAINER = Path(__file__).resolve().parent / "maintainer.json"
CONTROL_WINDOW = timedelta(hours=12)
INTERNAL_KINDS = frozenset({"robot_encoders_fk", "simulation_ground_truth"})
KIND_RE = re.compile(r"^[a-z0-9_]{1,40}$")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$")

LEDE = (
    "Each row is one run of a Bounded Embodiment test, run by the lab named, on its own "
    "machine, and submitted as a signed report. Before its pull request was merged, CI found "
    "it consistent: signed by a key registered for that lab, files matching their listed "
    "hashes, and the verdict following from the submitted data under the core version shown. "
    "Consistent is not passed. A FAIL report is consistent when its data supports FAIL, and "
    "it is listed here the same way. CI cannot check that the data came from the robot "
    "described, or that the envelope is adequate."
)
NOTES = (
    "Lab identity is self-asserted. Thresholds are illustrative. A result counts as reproduced "
    "only when a different lab reproduces it, and all of the maintainer's own projects count as "
    "one lab. These rows are measurements, not a rating of any robot, gate or product; do not "
    "cite them as evidence for any registry tier, standard or regulatory claim."
)
COLUMNS = (
    "Run (UTC)", "Test", "Verdict", "Role", "Lab", "Identity", "Instrument",
    "Control", "Reproduction", "Core", "Method",
)


@dataclass
class Row:
    result_id: str
    path: str
    lab: str
    lab_name: str
    identity_level: str
    maintainers_own: bool
    test: str
    verdict: str
    role: str
    started_at: str
    instrument: str
    core_version: str
    method_version: str
    reproduces: str
    withdrawn: bool
    control: str = "-"
    reproduction: str = "-"
    started: datetime | None = field(default=None, repr=False)
    envelope_hash: str = field(default="", repr=False)
    reproduced_by: list[tuple[str, str]] = field(default_factory=list, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "path": self.path,
            "test": self.test,
            "verdict": self.verdict,
            "withdrawn": self.withdrawn,
            "role": self.role,
            "lab": self.lab,
            "lab_name": self.lab_name,
            "maintainers_own": self.maintainers_own,
            "identity_level": self.identity_level,
            "instrument": self.instrument,
            "control": self.control,
            "reproduction": self.reproduction,
            "reproduces": self.reproduces or None,
            "core_version": self.core_version,
            "method_version": self.method_version,
            "started_at": self.started_at,
        }


def text(value: Any, limit: int = 120) -> str:
    """A short single-line string from a report value; "" for anything else."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    out = CONTROL_CHARS.sub(" ", str(value)).strip()
    return out if len(out) <= limit else out[: limit - 3] + "..."


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    match = ISO_RE.match(value)
    if not match:
        return None
    year, month, day, hour, minute, second, frac, zone = match.groups()
    try:
        moment = datetime(
            int(year), int(month), int(day), int(hour), int(minute), int(second),
            int((frac or "0")[:6].ljust(6, "0")),
        )
    except ValueError:
        return None
    offset = timedelta(0)
    if zone != "Z":
        sign = 1 if zone[0] == "+" else -1
        offset = sign * timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6]))
    return (moment - offset).replace(tzinfo=timezone.utc)


def instrument_class(report: dict[str, Any]) -> str:
    instruments = report.get("instruments")
    if not isinstance(instruments, list) or not instruments:
        return "none declared"
    for inst in instruments:
        kind = inst.get("kind") if isinstance(inst, dict) else None
        external = isinstance(inst, dict) and inst.get("external") is True
        if isinstance(kind, str) and KIND_RE.match(kind) and external and kind not in INTERNAL_KINDS:
            return "external: " + kind.replace("_", " ")
    first = instruments[0].get("kind") if isinstance(instruments[0], dict) else None
    if isinstance(first, str) and KIND_RE.match(first):
        return "internal: " + first.replace("_", " ")
    return "internal"


def load_maintainer(path: Path) -> tuple[set[str], set[str]]:
    doc = read_json(path)
    handles = doc.get("github_handles") if isinstance(doc, dict) else None
    labs = doc.get("labs") if isinstance(doc, dict) else None
    if not isinstance(handles, list) or not isinstance(labs, list):
        raise ToolError(f'{path}: needs "github_handles" and "labs" lists')
    return {h.lower() for h in handles if isinstance(h, str)}, {lab for lab in labs if isinstance(lab, str)}


def load_labs(repo: Path) -> dict[str, dict[str, Any]]:
    labs: dict[str, dict[str, Any]] = {}
    labs_dir = repo / "labs"
    if not labs_dir.is_dir():
        return labs
    for lab_dir in sorted(labs_dir.iterdir()):
        if not lab_dir.is_dir() or lab_dir.is_symlink() or not LAB_ID_RE.match(lab_dir.name):
            continue
        try:
            doc = read_json(lab_dir / "lab.json")
        except ToolError:
            continue
        if isinstance(doc, dict):
            labs[lab_dir.name] = doc
    return labs


def is_maintainers(lab: str, doc: dict[str, Any] | None, handles: set[str], labs: set[str]) -> bool:
    if lab in labs:
        return True
    listed = doc.get("github_handles") if doc else None
    return isinstance(listed, list) and any(isinstance(h, str) and h.lower() in handles for h in listed)


def collect(repo: Path, labs: dict[str, dict[str, Any]], maintainer: tuple[set[str], set[str]]):
    rows: list[Row] = []
    skipped: list[dict[str, str]] = []
    results_dir = repo / "results"
    if not results_dir.is_dir():
        return rows, skipped
    for lab_dir in sorted(results_dir.iterdir()):
        lab = lab_dir.name
        if not lab_dir.is_dir() or lab_dir.is_symlink() or not LAB_ID_RE.match(lab) or is_dry_run(lab):
            continue
        doc = labs.get(lab)
        for run_dir in sorted(lab_dir.iterdir()):
            if not run_dir.is_dir() or run_dir.is_symlink() or not RUN_DIR_RE.match(run_dir.name):
                continue
            rel = f"results/{lab}/{run_dir.name}"
            try:
                report = read_json(run_dir / "report.json")
            except ToolError as exc:
                skipped.append({"path": rel, "reason": str(exc).replace(str(repo) + os.sep, "")})
                continue
            if not isinstance(report, dict):
                skipped.append({"path": rel, "reason": "report.json is not an object"})
                continue
            verdict = dig(report, "verdict", "result")
            role = report.get("role")
            reproduces = report.get("reproduces")
            rows.append(
                Row(
                    result_id=f"{lab}/{run_dir.name}",
                    path=rel,
                    lab=lab,
                    lab_name=text(doc.get("name"), 80) if doc else "",
                    identity_level="self-asserted" if doc else "unregistered",
                    maintainers_own=is_maintainers(lab, doc, *maintainer),
                    test=text(report.get("test"), 40) or "?",
                    verdict=verdict if verdict in VERDICTS else "unknown",
                    role=role if role in ROLES else "unknown",
                    started_at=text(dig(report, "run", "started_at"), 40),
                    instrument=instrument_class(report),
                    core_version=text(dig(report, "provenance", "core_version"), 40) or "?",
                    method_version=text(dig(report, "method", "version"), 40) or "?",
                    reproduces=text(reproduces, 120) if isinstance(reproduces, str) else "",
                    withdrawn=(run_dir / "WITHDRAWN.md").is_file(),
                    started=parse_time(dig(report, "run", "started_at")),
                    envelope_hash=text(dig(report, "envelope", "hash"), 100),
                )
            )
    return rows, skipped


def pair_controls(rows: list[Row]) -> None:
    """Each test run gets the nearest control by the same lab, same test, same envelope, within 12 h."""
    controls = [r for r in rows if r.role == "control" and not r.withdrawn]
    for row in rows:
        if row.role != "test":
            continue
        best: tuple[tuple[timedelta, str], Row] | None = None
        for ctl in controls:
            if (
                ctl.lab != row.lab
                or ctl.test != row.test
                or not row.envelope_hash
                or ctl.envelope_hash != row.envelope_hash
                or row.started is None
                or ctl.started is None
            ):
                continue
            gap = abs(ctl.started - row.started)
            if gap <= CONTROL_WINDOW and (best is None or (gap, ctl.result_id) < best[0]):
                best = ((gap, ctl.result_id), ctl)
        if best is None:
            row.control = "no control"
        elif best[1].verdict == "FAIL":
            row.control = "control failed"
        else:
            row.control = "control did not fail"


def link_reproductions(rows: list[Row]) -> None:
    """Only a test run by a different party counts; the maintainer's projects are one party."""

    def party(row: Row) -> str:
        return "maintainer" if row.maintainers_own else "lab:" + row.lab

    by_id = {r.result_id: r for r in rows}
    for row in rows:
        if not row.reproduces:
            continue
        target = by_id.get(row.reproduces)
        name = row.reproduces
        if row.withdrawn:
            row.reproduction = f"names {name}; withdrawn, not counted"
        elif target is None:
            row.reproduction = f"names {name}, which is not listed here; not counted"
        elif target.withdrawn:
            row.reproduction = f"names {name}, which is withdrawn; not counted"
        elif row.role != "test" or target.role != "test":
            row.reproduction = f"names {name}; only test runs count as reproductions"
        elif target.test != row.test:
            row.reproduction = f"names {name}, a different test; not counted"
        elif party(target) == party(row):
            row.reproduction = f"re-run of {name} by the same lab; not a reproduction"
        else:
            row.reproduction = f"reproduction of {name}"
            target.reproduced_by.append((row.lab, row.verdict))
    for row in rows:
        if row.reproduces or row.role != "test":
            continue
        if row.reproduced_by:
            row.reproduction = "; ".join(f"reproduction by {lab}: {v}" for lab, v in sorted(row.reproduced_by))
        else:
            row.reproduction = "not reproduced"


def build(repo: Path, maintainer_path: Path = DEFAULT_MAINTAINER) -> tuple[list[Row], list[dict[str, str]]]:
    labs = load_labs(repo)
    rows, skipped = collect(repo, labs, load_maintainer(maintainer_path))
    pair_controls(rows)
    link_reproductions(rows)
    rows.sort(key=lambda r: r.result_id)
    rows.sort(key=lambda r: r.started or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return rows, skipped


def _when(row: Row) -> str:
    if row.started is None:
        return row.started_at or "?"
    return row.started.strftime("%Y-%m-%d %H:%M")


def render_html(rows: list[Row], repository: str, commit: str | None) -> str:
    e = html.escape
    base = f"https://github.com/{repository}"
    body: list[str] = []
    for row in rows:
        lab_cell = e(row.lab)
        if row.lab_name:
            lab_cell = f'{e(row.lab_name)}<span class="sub">{e(row.lab)}</span>'
        if row.maintainers_own:
            lab_cell += f'<span class="tag">{e(MAINTAINER_LABEL)}</span>'
        verdict = f'<span class="v v-{e(row.verdict)}">{e(row.verdict)}</span>'
        if row.withdrawn:
            verdict += '<span class="tag">withdrawn</span>'
        run_link = f'{base}/tree/main/{quote(row.path)}'
        cells = [
            f'<a href="{e(run_link)}">{e(_when(row))}</a><span class="sub">{e(row.result_id)}</span>',
            e(row.test),
            verdict,
            e(row.role),
            lab_cell,
            e(row.identity_level),
            e(row.instrument),
            e(row.control),
            e(row.reproduction),
            e(row.core_version),
            e(row.method_version),
        ]
        wide = {0, 4, 6, 8}
        tds = "".join(f'<td class="wrap">{c}</td>' if i in wide else f"<td>{c}</td>" for i, c in enumerate(cells))
        body.append(f"<tr>{tds}</tr>")
    if not body:
        body.append(f'<tr><td colspan="{len(COLUMNS)}">No results yet.</td></tr>')
    head = "".join(f'<th scope="col">{e(c)}</th>' for c in COLUMNS)
    source = (
        f'Built from <a href="{e(base)}/commit/{e(commit)}">{e(commit[:12])}</a>.' if commit else "Built from main."
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(TITLE)}</title>
<style>
:root {{ --bg:#fbfaf7; --fg:#1c1c1a; --muted:#5d5b55; --rule:#dcd8cf; --tag:#ebe7dd;
  --fail:#a12d1d; --pass:#1d6038; --inc:#6d5a0f; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#141413; --fg:#eceae4; --muted:#a8a59c; --rule:#3a3934; --tag:#2b2a26;
    --fail:#f2907f; --pass:#8fd4a9; --inc:#e3cf7c; }}
}}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg);
  font:15px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
main {{ max-width:76rem; margin:0 auto; padding:2rem 16px 3rem; }}
h1 {{ font-size:1.5rem; line-height:1.2; margin:0 0 1rem; }}
.lede p {{ max-width:46rem; margin:0 0 .8rem; }}
.lede p:first-child {{ font-size:1.02rem; }}
.scroll {{ overflow-x:auto; margin-top:1.5rem; border-top:1px solid var(--rule); }}
table {{ border-collapse:collapse; width:100%; font-size:.86rem; }}
th, td {{ text-align:left; vertical-align:top; padding:.5rem .75rem .5rem 0;
  border-bottom:1px solid var(--rule); white-space:nowrap; }}
th {{ font-weight:600; color:var(--muted); }}
td.wrap {{ white-space:normal; min-width:11rem; }}
.sub {{ display:block; color:var(--muted); font-size:.78rem; overflow-wrap:anywhere; }}
.tag {{ display:inline-block; background:var(--tag); border-radius:3px; padding:0 .35rem;
  margin:.15rem .3rem 0 0; font-size:.76rem; white-space:nowrap; }}
.v {{ font-weight:600; margin-right:.35rem; }}
.v-FAIL {{ color:var(--fail); }} .v-PASS {{ color:var(--pass); }} .v-INCONCLUSIVE {{ color:var(--inc); }}
a {{ color:inherit; text-underline-offset:2px; }}
footer {{ max-width:46rem; margin-top:2rem; color:var(--muted); font-size:.84rem; }}
footer p {{ margin:0 0 .5rem; }}
</style>
</head>
<body>
<main>
<h1>{e(TITLE)}</h1>
<div class="lede">
<p>{e(LEDE)}</p>
<p>{e(NOTES)} <a href="{e(base)}/blob/main/POLICY.md">POLICY.md</a></p>
</div>
<div class="scroll">
<table>
<thead><tr>{head}</tr></thead>
<tbody>
{chr(10).join(body)}
</tbody>
</table>
</div>
<footer>
<p>Dry-run labs (test-*) are not listed. A withdrawn run stays listed and no longer counts
as a control or a reproduction.</p>
<p>Data: CC BY 4.0, granted in each lab's signed report. Tooling: Apache-2.0. The method's
maintainer also maintains RCAN; the method does not depend on it.</p>
<p><a href="{e(base)}">{e(repository)}</a> &middot; <a href="results.json">results.json</a> &middot; {source}</p>
</footer>
</main>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", type=Path, default=Path("."), help="repository root (default: .)")
    parser.add_argument("--out", type=Path, default=Path("site"), help="output directory (default: site)")
    parser.add_argument("--maintainer", type=Path, default=DEFAULT_MAINTAINER, help="maintainer.json")
    args = parser.parse_args(argv)
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not REPOSITORY_RE.match(repository):
        repository = DEFAULT_REPOSITORY
    commit = os.environ.get("GITHUB_SHA", "")
    commit = commit if SHA_RE.match(commit) else None
    try:
        rows, skipped = build(args.repo, args.maintainer)
    except ToolError as exc:
        print(f"build-table: could not run: {exc}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "index.html").write_text(render_html(rows, repository, commit), encoding="utf-8")
    payload = {
        "title": TITLE,
        "statement": LEDE,
        "notes": NOTES,
        "repository": repository,
        "source_commit": commit,
        "rows": [r.public() for r in rows],
        "skipped": skipped,
    }
    (args.out / "results.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    for item in skipped:
        print(f"build-table: skipped {item['path']}: {item['reason']}", file=sys.stderr)
    print(f"build-table: {len(rows)} row(s) written to {args.out}/index.html and {args.out}/results.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
