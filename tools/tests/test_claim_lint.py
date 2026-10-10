from __future__ import annotations

import json

import pytest

import claim_lint
from conftest import RUN, make_report

REQUIRED_WORDS = {
    "certified", "certification", "compliant", "compliance", "conformant", "conformance", "approved",
    "endorsed", "endorsement", "accredited", "guaranteed", "safe", "BE-1", "BE-2", "BE-3",
}


@pytest.fixture(scope="module")
def patterns():
    return claim_lint.compile_words(claim_lint.load_words())


def test_the_public_word_list_is_exactly_the_policy_list():
    words = claim_lint.load_words()
    assert set(words) == REQUIRED_WORDS and len(words) == len(REQUIRED_WORDS)
    assert "safety" in claim_lint.DEFAULT_WORDS.read_text()  # the note that "safety" is allowed


@pytest.mark.parametrize(
    "text, hits",
    [
        ("This gate is Certified.", ["certified"]),
        ("CERTIFICATION pending", ["certification"]),
        ("a safe arm", ["safe"]),
        ("a fail-safe stop", ["safe"]),
        ("meets BE-1", ["BE-1"]),
        ("meets be-2", ["BE-2"]),
        ("BE‑3 (non-breaking hyphen)", ["BE-3"]),
        ("ｃｅｒｔｉｆｉｅｄ in full-width letters", ["certified"]),
        ("safety stop; unsafe command rejected; stops safely", []),
        ("BE-10 is not a level name, and neither is ABE-1", []),
        ("pre-approved", ["approved"]),
        ("approvedly is not a word, and endorsements is a different one", []),
        ("zero samples outside the envelope; illustrative thresholds; not reproduced", []),
    ],
)
def test_whole_word_case_insensitive_matching(patterns, text, hits):
    assert claim_lint.find_words(text, patterns) == hits


def test_report_string_values_are_reported_by_field(tmp_path, patterns):
    run = tmp_path / RUN
    run.mkdir()
    report = make_report(
        system_under_test__gate__name="SafeArm gate (certified)",
        instruments=[{"kind": "optical_marker_tracker", "external": True, "calibration": "guaranteed to 1 mm"}],
    )
    report["safe"] = "object keys are not scanned, only values"
    report["statements"]["not_certification"] = True
    report["signature"] = [{"alg": "Ed25519", "kid": "safe", "sig": "-safe-"}]
    report["odd key"] = ["fine", "BE-2 arm"]
    (run / "report.json").write_text(json.dumps(report))
    hits, problems = claim_lint.lint_run(run, patterns)
    assert problems == []
    assert sorted(hits) == sorted(
        [
            f'{run}/report.json:system_under_test.gate.name: "certified"',
            f'{run}/report.json:instruments[0].calibration: "guaranteed"',
            f'{run}/report.json:[\'odd key\'][1]: "BE-2"',
        ]
    )


def test_notes_are_reported_by_line(tmp_path, patterns):
    run = tmp_path / RUN
    (run / "notes").mkdir(parents=True)
    (run / "report.json").write_text(json.dumps(make_report()))
    (run / "notes" / "NOTES.md").write_text("Setup notes\n\nThe arm is safe now.\nSafety stop wired.\n")
    hits, problems = claim_lint.lint_run(run, patterns)
    assert hits == [f'{run}/notes/NOTES.md:3: "safe"'] and problems == []


def test_unreadable_or_missing_reports_fail(tmp_path, patterns):
    dup = tmp_path / "dup"
    dup.mkdir()
    (dup / "report.json").write_text('{"test": "EV-03", "test": "EV-10"}')
    assert claim_lint.lint_run(dup, patterns)[1]
    missing = tmp_path / "missing"
    missing.mkdir()
    assert claim_lint.lint_run(missing, patterns)[1]


def test_main_exit_status(tmp_path, capsys):
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "report.json").write_text(json.dumps(make_report()))
    assert claim_lint.main([str(clean)]) == 0
    dirty = tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "report.json").write_text(json.dumps(make_report(test="EV-03 (accredited)")))
    assert claim_lint.main([str(clean), str(dirty)]) == 1
    assert 'report.json:test: "accredited"' in capsys.readouterr().out
