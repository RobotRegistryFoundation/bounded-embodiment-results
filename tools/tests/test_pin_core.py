from __future__ import annotations

import json

import pytest

import pin_core
from conftest import REPO_ROOT, RUN, make_report

HASH = "3" * 64


@pytest.fixture
def lock(tmp_path):
    path = tmp_path / "kits.lock"
    path.write_text(json.dumps({"bounded-embodiment": {"0.1.0": {"wheel_sha256": HASH, "released": "2026-10-13"}}}))
    return path


#: Entries are never edited or removed, so earlier results stay checkable (README, kits.lock).
PUBLISHED = {
    "0.1.0": {"wheel_sha256": "eecc81ffb6867efb86d33c70cc36804dc00bdc6b778f14c35d3debf6f048e0cb", "released": "2026-10-12"},
}


def test_the_committed_lock_keeps_every_published_core_and_refuses_the_rest():
    lock = json.loads((REPO_ROOT / "kits.lock").read_text())
    for version, entry in PUBLISHED.items():
        assert lock["bounded-embodiment"][version] == entry
    entries = pin_core.load_lock(REPO_ROOT / "kits.lock")
    line = pin_core.requirement_line(entries, "0.1.0")
    assert line == f"bounded-embodiment==0.1.0 --hash=sha256:{PUBLISHED['0.1.0']['wheel_sha256']}\n"
    with pytest.raises(pin_core.Refused, match="not in kits.lock"):
        pin_core.requirement_line(entries, "0.0.9")


def test_a_listed_version_gives_a_hash_pinned_requirement(lock):
    line = pin_core.requirement_line(pin_core.load_lock(lock), "0.1.0")
    assert line == f"bounded-embodiment==0.1.0 --hash=sha256:{HASH}\n"


@pytest.mark.parametrize("version", ["0.1.1", "0.1.0.dev0", "0.1.0+local", "0.1", "latest", "0.1.0 --index-url x", 1])
def test_unlisted_unreleased_and_odd_versions_are_refused(lock, version):
    with pytest.raises(pin_core.Refused):
        pin_core.requirement_line(pin_core.load_lock(lock), version)


@pytest.mark.parametrize(
    "doc",
    [
        {"bounded-embodiment": {"0.1.0": {"wheel_sha256": "ABC", "released": "2026-10-13"}}},
        {"bounded-embodiment": {"0.1.0": {"wheel_sha256": HASH}}},
        {"bounded-embodiment": {"0.1.0": {"wheel_sha256": HASH, "released": "2026-10-13", "url": "x"}}},
        {"bounded-embodiment": {"0.1.0.dev0": {"wheel_sha256": HASH, "released": "2026-10-13"}}},
        {"bounded-embodiment": {}, "bounded-embodiment-kit": {}},
        {"bounded-embodiment": []},
    ],
)
def test_malformed_locks_cannot_be_used(tmp_path, doc):
    path = tmp_path / "kits.lock"
    path.write_text(json.dumps(doc))
    with pytest.raises(pin_core.ToolError):
        pin_core.load_lock(path)


def test_main_reads_the_version_from_a_run(tmp_path, lock, capsys):
    run = tmp_path / RUN
    run.mkdir()
    (run / "report.json").write_text(json.dumps(make_report()))
    out = tmp_path / "req.txt"
    assert pin_core.main(["--lock", str(lock), "--run-dir", str(run), "--out", str(out)]) == 0
    assert capsys.readouterr().out == "0.1.0\n"
    assert out.read_text() == f"bounded-embodiment==0.1.0 --hash=sha256:{HASH}\n"


def test_main_refuses_an_unlisted_version(tmp_path, lock, capsys):
    run = tmp_path / RUN
    run.mkdir()
    (run / "report.json").write_text(json.dumps(make_report(provenance__core_version="0.2.0")))
    assert pin_core.main(["--lock", str(lock), "--run-dir", str(run)]) == 1
    assert "not in kits.lock" in capsys.readouterr().err


def test_duplicate_keys_in_a_report_cannot_steer_the_version(tmp_path, lock):
    run = tmp_path / RUN
    run.mkdir()
    (run / "report.json").write_text(
        '{"provenance": {"core_version": "0.1.0"}, "provenance": {"core_version": "0.0.9"}}'
    )
    assert pin_core.main(["--lock", str(lock), "--run-dir", str(run)]) == 2
