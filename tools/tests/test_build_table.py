from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import build_table
from conftest import make_key, make_lab, make_report

FORBIDDEN = re.compile(
    r"(?<!\w)(verified|certified|certification|compliant|compliance|conformant|conformance|approved|"
    r"endorsed|endorsement|accredited|guaranteed|safe|BE-[123])(?!\w)",
    re.IGNORECASE,
)
H1 = "sha256:" + "1" * 64
H2 = "sha256:" + "2" * 64


def put(root: Path, lab: str, run: str, withdrawn: bool = False, **overrides) -> None:
    d = root / "results" / lab / run
    d.mkdir(parents=True)
    (d / "report.json").write_text(json.dumps(make_report(**overrides)))
    (d / "envelope.json").write_text(json.dumps({"envelope_version": "0.2", "declared_level": "BE-2"}))
    if withdrawn:
        (d / "WITHDRAWN.md").write_text("2026-10-20: withdrawn by the lab.\n")


@pytest.fixture
def site(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    labs = {
        "acme": make_lab("acme", ["acme-bot"], [make_key(1, "acme")], name="Acme Robotics"),
        "craig-lab": make_lab("craig-lab", ["CraigM26"], [make_key(2, "craig-lab")], name="Bench at home"),
        "opencastor": make_lab("opencastor", ["castor-bot"], [make_key(3, "opencastor")]),
        "test-dry": make_lab("test-dry", ["second-account"], [make_key(4, "test-dry")]),
    }
    for lab, doc in labs.items():
        (root / "labs" / lab).mkdir(parents=True)
        (root / "labs" / lab / "lab.json").write_text(json.dumps(doc))
    (root / "maintainer.json").write_text(json.dumps({"github_handles": ["craigm26"], "labs": ["opencastor"]}))

    # acme: a test run, its control 1 h earlier (FAIL), a withdrawn EV-10 run.
    put(root, "acme", "ev03-20261013T100000Z-s7", envelope__hash=H1, run__started_at="2026-10-13T10:00:00Z")
    put(root, "acme", "ev03-20261013T090000Z-s7", role="control", envelope__hash=H1,
        run__started_at="2026-10-13T09:00:00Z")
    put(root, "acme", "ev10-20261016T100000Z-s1", withdrawn=True, test="EV-10", verdict__result="PASS",
        run__started_at="2026-10-16T10:00:00Z")
    # The maintainer reproduces acme's run (a different party): counts.
    put(root, "craig-lab", "ev03-20261014T100000Z-s7", verdict__result="PASS", envelope__hash=H2,
        reproduces="acme/ev03-20261013T100000Z-s7", run__started_at="2026-10-14T10:00:00Z")
    # Another maintainer project "reproduces" the maintainer's run: same party, does not count.
    # Its control came out INCONCLUSIVE.
    put(root, "opencastor", "ev03-20261015T100000Z-s7", verdict__result="INCONCLUSIVE", envelope__hash=H2,
        reproduces="craig-lab/ev03-20261014T100000Z-s7", run__started_at="2026-10-15T10:00:00+02:00",
        instruments=[{"kind": "robot_encoders_fk", "external": False}])
    put(root, "opencastor", "ev03-20261015T050000Z-s7", role="control", verdict__result="INCONCLUSIVE",
        envelope__hash=H2, run__started_at="2026-10-15T05:00:00Z")
    # Hostile strings and a dry-run lab.
    put(root, "acme", "ev03-20261017T100000Z-s9", test="<script>alert(1)</script>",
        provenance__core_version='0.1.0"><img src=x>', run__started_at="2026-10-17T10:00:00Z")
    put(root, "test-dry", "ev03-20261018T100000Z-s7", run__started_at="2026-10-18T10:00:00Z")
    # Unreadable report: skipped, not fatal.
    bad = root / "results" / "acme" / "ev03-20261019T100000Z-s7"
    bad.mkdir(parents=True)
    (bad / "report.json").write_text("{not json")
    return root


def build(root: Path):
    return build_table.build(root, root / "maintainer.json")


def by_id(rows):
    return {r.result_id: r for r in rows}


def test_rows_controls_reproductions_and_labels(site: Path):
    rows, skipped = build(site)
    r = by_id(rows)
    assert "test-dry/ev03-20261018T100000Z-s7" not in r
    assert [s["path"] for s in skipped] == ["results/acme/ev03-20261019T100000Z-s7"]

    acme = r["acme/ev03-20261013T100000Z-s7"]
    assert (acme.verdict, acme.role, acme.control) == ("FAIL", "test", "control failed")
    assert acme.reproduction == "reproduction by craig-lab: PASS"
    assert acme.identity_level == "self-asserted" and not acme.maintainers_own
    assert acme.instrument == "external: optical marker tracker"

    craig = r["craig-lab/ev03-20261014T100000Z-s7"]
    assert craig.maintainers_own and craig.reproduction == "reproduction of acme/ev03-20261013T100000Z-s7"
    assert craig.control == "no control"

    castor = r["opencastor/ev03-20261015T100000Z-s7"]
    assert castor.maintainers_own
    assert castor.reproduction.startswith("re-run of craig-lab/") and "not a reproduction" in castor.reproduction
    assert castor.control == "control did not fail"
    assert castor.instrument == "internal: robot encoders fk"

    withdrawn = r["acme/ev10-20261016T100000Z-s1"]
    assert withdrawn.withdrawn and withdrawn.verdict == "PASS"

    assert r["acme/ev03-20261013T090000Z-s7"].control == "-"  # controls aren't paired with themselves


def test_newest_runs_come_first(site: Path):
    rows, _ = build(site)
    starts = [r.started for r in rows]
    assert starts == sorted(starts, reverse=True)


def test_html_has_the_statement_no_level_column_and_no_claim_words(site: Path, tmp_path: Path):
    rows, _ = build(site)
    page = build_table.render_html(rows, "RobotRegistryFoundation/bounded-embodiment-results", None)
    assert "Consistent is not passed." in page
    assert "CI cannot check that the data came from the robot described, or that the envelope is adequate." in page
    assert "self-asserted" in page and "maintainer&#x27;s own" in page
    header = re.search(r"<thead>(.*?)</thead>", page, re.S).group(1)
    assert "level" not in header.lower() and "BE" not in header
    assert "declared_level" not in page
    assert not FORBIDDEN.search(page), FORBIDDEN.search(page)
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert '"><img' not in page


def test_main_writes_both_files_deterministically(site: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "RobotRegistryFoundation/bounded-embodiment-results")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    out1, out2 = tmp_path / "site1", tmp_path / "site2"
    args = ["--repo", str(site), "--maintainer", str(site / "maintainer.json")]
    assert build_table.main(args + ["--out", str(out1)]) == 0
    assert build_table.main(args + ["--out", str(out2)]) == 0
    for name in ("index.html", "results.json"):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes()
    data = json.loads((out1 / "results.json").read_text())
    assert data["source_commit"] == "a" * 40
    assert set(data["rows"][0]) == {
        "result_id", "path", "test", "verdict", "withdrawn", "role", "lab", "lab_name", "maintainers_own",
        "identity_level", "instrument", "control", "reproduction", "reproduces", "core_version",
        "method_version", "started_at",
    }
    assert not FORBIDDEN.search((out1 / "results.json").read_text().replace("<script>alert(1)</script>", ""))


def test_an_empty_repository_builds_an_empty_table(tmp_path: Path):
    from conftest import TOOLS

    (tmp_path / "results").mkdir()
    assert build_table.main(["--repo", str(tmp_path), "--out", str(tmp_path / "site"),
                             "--maintainer", str(TOOLS / "maintainer.json")]) == 0
    page = (tmp_path / "site" / "index.html").read_text()
    assert "No results yet." in page and not FORBIDDEN.search(page)


def test_the_committed_maintainer_file_names_the_maintainer():
    from conftest import TOOLS

    handles, labs = build_table.load_maintainer(TOOLS / "maintainer.json")
    assert "craigm26" in handles
