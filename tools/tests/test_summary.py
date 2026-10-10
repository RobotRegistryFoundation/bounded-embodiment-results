from __future__ import annotations

import json
import re

import pytest

import summary
from _common import CONSISTENT_SENTENCE
from conftest import RUN, make_report

EXACT = (
    "Consistent: signed by a registered key, files match, and the verdict follows from the submitted "
    "data under core 0.1.0. CI cannot check that the data came from the robot described, or that the "
    "envelope is adequate."
)
FORBIDDEN = re.compile(
    r"(?<!\w)(verified|certified|certification|compliant|compliance|conformant|conformance|approved|"
    r"endorsed|endorsement|accredited|guaranteed|safe|BE-[123])(?!\w)",
    re.IGNORECASE,
)


def needs(kind="results", run_dirs="", withdrawals="", lab="acme", flags="", **results):
    data = {
        "path-guard": {"result": "success", "outputs": {"kind": kind, "lab": lab, "scope": f"results/{lab}",
                                                       "run_dirs": run_dirs, "withdrawals": withdrawals}},
        "identity": {"result": "success", "outputs": {}},
        "be-verify": {"result": "success" if run_dirs else "skipped", "outputs": {"flags": flags}},
        "claim-lint": {"result": "success" if run_dirs else "skipped", "outputs": {}},
    }
    for job, result in results.items():
        data[job.replace("_", "-")]["result"] = result
    return data


@pytest.fixture
def checkout(tmp_path):
    def add(run: str, **overrides):
        d = tmp_path / "results" / "acme" / run
        d.mkdir(parents=True)
        (d / "report.json").write_text(json.dumps(make_report(**overrides)))
        return f"results/acme/{run}"

    add.root = tmp_path
    return add


def test_the_sentence_is_the_one_in_the_policy():
    assert CONSISTENT_SENTENCE.format(core="0.1.0") == EXACT


def test_a_consistent_fail_report_gets_a_green_summary_that_starts_with_the_sentence(checkout):
    run_dir = checkout(RUN)
    ok, text = summary.render(needs(run_dirs=run_dir), checkout.root)
    assert ok
    assert text.startswith(EXACT + "\n")
    assert f"| `acme/{RUN}` | EV-03 | test | FAIL | 0.1.0 | - |" in text
    assert "not passed" in text
    assert not FORBIDDEN.search(text), FORBIDDEN.search(text)


def test_several_core_versions_are_all_named(checkout):
    a = checkout(RUN)
    b = checkout("ev03-20261014T090000Z-s11", provenance__core_version="0.1.10")
    c = checkout("ev10-20261014T100000Z-s1", provenance__core_version="0.1.9", test="EV-10")
    ok, text = summary.render(needs(run_dirs=f"{a} {b} {c}"), checkout.root)
    assert ok and "under core 0.1.0, 0.1.9 and 0.1.10." in text.splitlines()[0]


@pytest.mark.parametrize(
    "overrides, missing",
    [
        ({"be_verify": "failure"}, "be-verify (failure)"),
        ({"claim_lint": "failure"}, "claim-lint (failure)"),
        ({"identity": "failure", "be_verify": "skipped", "claim_lint": "skipped"}, "identity (failure)"),
        ({"be_verify": "skipped"}, "be-verify (did not run)"),
        ({"be_verify": "cancelled"}, "be-verify (cancelled)"),
    ],
)
def test_anything_short_of_every_check_is_not_consistent(checkout, overrides, missing):
    ok, text = summary.render(needs(run_dirs=checkout(RUN), **overrides), checkout.root)
    assert not ok
    assert text.startswith("Not consistent.") and missing in text
    assert "Consistent:" not in text


def test_a_failed_path_guard_is_not_consistent(checkout):
    data = {"path-guard": {"result": "failure", "outputs": {}}, "identity": {"result": "skipped"},
            "be-verify": {"result": "skipped"}, "claim-lint": {"result": "skipped"}}
    ok, text = summary.render(data, checkout.root)
    assert not ok and "path-guard (failure)" in text
    assert summary.render({}, checkout.root)[0] is False


def test_lab_changes_and_withdrawals_have_their_own_wording(checkout):
    ok, text = summary.render(needs(kind="lab"), checkout.root)
    assert ok and text.startswith("Lab change for `acme`") and "self-asserted" in text
    ok, text = summary.render(needs(withdrawals=f"results/acme/{RUN}"), checkout.root)
    assert ok and text.startswith(f"Withdrawal notice for `acme/{RUN}`")
    for t in (text, summary.render(needs(kind="lab"), checkout.root)[1]):
        assert "Consistent:" not in t and not FORBIDDEN.search(t)


def test_hostile_report_strings_cannot_break_the_table(checkout):
    run_dir = checkout(RUN, test="EV-03 | <img src=x onerror=alert(1)> \n injected")
    ok, text = summary.render(needs(run_dirs=run_dir), checkout.root)
    assert ok
    row = next(line for line in text.splitlines() if line.startswith(f"| `acme/{RUN}`"))
    assert row.count("|") == 7 and "<" not in row


def test_flags_are_collected_from_any_shape_of_be_verify_output(tmp_path, capsys, checkout):
    out = tmp_path / "be-verify"
    out.mkdir()
    (out / f"acme__{RUN}.json").write_text(json.dumps({
        "valid": True,
        "findings": [
            {"code": "FACE_NEVER_APPROACHED", "severity": "warn", "detail": "face x_max", "where": "/coverage"},
            {"code": "SIGNATURE_OK", "severity": "info", "detail": "ok"},
        ],
        "nested": {"more": [{"code": "TOLERANCE_WIDE", "severity": "warn", "detail": "tol > 2x p95"}]},
    }))
    (out / "acme__ev03-20261014T090000Z-s11.json").write_text("not json")
    assert summary.main(["flags", str(out)]) == 0
    line = capsys.readouterr().out.strip()
    assert line.startswith("flags=") and "\n" not in line
    flags = json.loads(line.removeprefix("flags="))
    assert [f["code"] for f in flags[f"acme/{RUN}"]] == ["FACE_NEVER_APPROACHED", "TOLERANCE_WIDE"]

    ok, text = summary.render(needs(run_dirs=checkout(RUN), flags=json.dumps(flags)), checkout.root)
    assert ok and "FACE_NEVER_APPROACHED" in text and "never block" in text


def test_main_render_writes_the_summary_and_sets_the_exit_status(checkout, monkeypatch, tmp_path):
    run_dir = checkout(RUN)
    target = tmp_path / "step_summary.md"
    monkeypatch.setenv("NEEDS", json.dumps(needs(run_dirs=run_dir)))
    assert summary.main(["render", "--repo", str(checkout.root), "--out", str(target)]) == 0
    assert target.read_text().startswith(EXACT)
    monkeypatch.setenv("NEEDS", json.dumps(needs(run_dirs=run_dir, claim_lint="failure")))
    assert summary.main(["render", "--repo", str(checkout.root)]) == 1
    monkeypatch.setenv("NEEDS", "not json")
    assert summary.main(["render", "--repo", str(checkout.root)]) == 1
