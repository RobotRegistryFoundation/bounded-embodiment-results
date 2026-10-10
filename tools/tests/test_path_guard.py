from __future__ import annotations

import io
import json

import pytest

import path_guard
from conftest import RUN, Repo, make_key, make_lab
from path_guard import Change, classify

RUN_DIR = f"results/acme/{RUN}"
NEW_RUN = [
    Change(f"{RUN_DIR}/report.json", "A", "100644"),
    Change(f"{RUN_DIR}/envelope.json", "A", "100644"),
    Change(f"{RUN_DIR}/raw/samples.csv.gz", "A", "100644"),
]


def not_on_base(_: str) -> bool:
    return False


def on_base(_: str) -> bool:
    return True


# ---------------------------------------------------------------- classification


def test_new_runs_for_one_lab_are_a_results_submission():
    verdict = classify(NEW_RUN, run_on_base=not_on_base)
    assert verdict.ok, verdict.problems
    assert (verdict.kind, verdict.lab, verdict.scope) == ("results", "acme", "results/acme")
    assert verdict.run_dirs == [RUN_DIR]
    assert "Results submission for lab \"acme\"" in verdict.describe()


def test_lab_registration_is_its_own_kind():
    verdict = classify([Change("labs/acme/lab.json", "A", "100644")], lab_on_base=lambda lab: False)
    assert verdict.ok
    assert (verdict.kind, verdict.scope, verdict.lab_on_base) == ("lab", "labs/acme", False)
    assert "new lab registration" in verdict.describe()


def test_dry_run_labs_are_accepted():
    verdict = classify([Change(f"results/test-second/{RUN}/report.json", "A")], run_on_base=not_on_base)
    assert verdict.ok and verdict.lab == "test-second"


def test_two_labs_are_rejected():
    changes = NEW_RUN + [Change(f"results/other/{RUN}/report.json", "A")]
    verdict = classify(changes, run_on_base=not_on_base)
    assert not verdict.ok
    assert any("more than one lab" in p for p in verdict.problems)
    assert verdict.as_dict()["run_dirs"] == [] and verdict.scope == ""


def test_results_and_lab_change_together_are_rejected():
    verdict = classify(NEW_RUN + [Change("labs/acme/lab.json", "M")], run_on_base=not_on_base)
    assert not verdict.ok
    assert any("not both" in p for p in verdict.problems)


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/verify.yml",
        ".github/CODEOWNERS",
        "tools/path_guard.py",
        "tools/claim-words.txt",
        "kits.lock",
        "POLICY.md",
        "CODEOWNERS",
        "README.md",
        "REUSE.toml",
        ".gitattributes",
        "results/README.md",
        "labs/README.md",
    ],
)
def test_maintainer_paths_are_rejected_alone_and_with_a_submission(path):
    alone = classify([Change(path, "M", "100644")], run_on_base=not_on_base)
    mixed = classify(NEW_RUN + [Change(path, "M", "100644")], run_on_base=not_on_base)
    assert not alone.ok and not mixed.ok
    assert any(path in p for p in mixed.problems)


def test_workflow_edit_is_flagged_by_the_path_guard():
    verdict = classify(NEW_RUN + [Change(".github/workflows/verify.yml", "M", "100644")], run_on_base=not_on_base)
    assert not verdict.ok
    assert "maintainer only" in verdict.describe()


@pytest.mark.parametrize(
    "path, why",
    [
        ("results/acme/notes.md", "inside results/<lab>/<run>/"),
        (f"results/acme/{RUN}", "inside results/<lab>/<run>/"),
        (f"results/_template/{RUN}/report.json", "not a valid lab id"),
        (f"results/Acme/{RUN}/report.json", "not a valid lab id"),
        ("results/acme/ev03-2026-10-13-s7/report.json", "not a run directory name"),
        ("results/acme/EV-03-20261013T183000Z-s7/report.json", "not a run directory name"),
        (f"results/acme/{RUN}/.gitattributes", "must not start with '.'"),
        (f"results/acme/{RUN}/raw/data file.csv", "must not start with '.'"),
        (f"results/acme/{RUN}/../../../tools/x.py", "not a normalized path"),
        ("/etc/passwd", "not a relative path"),
        ("labs/_template/lab.json", "not a valid lab id"),
        ("labs/acme", "inside labs/<lab>/"),
    ],
)
def test_misplaced_paths_are_rejected(path, why):
    verdict = classify([Change(path, "A", "100644")], run_on_base=not_on_base)
    assert not verdict.ok
    assert any(why in p for p in verdict.problems), verdict.problems


@pytest.mark.parametrize("mode, word", [("120000", "symbolic links"), ("160000", "submodules")])
def test_symlinks_and_submodules_are_rejected(mode, word):
    verdict = classify(NEW_RUN + [Change(f"{RUN_DIR}/raw/link", "A", mode)], run_on_base=not_on_base)
    assert any(word in p for p in verdict.problems)


def test_withdrawal_notice_for_a_run_on_main():
    verdict = classify([Change(f"{RUN_DIR}/WITHDRAWN.md", "A", "100644")], run_on_base=on_base)
    assert verdict.ok
    assert verdict.withdrawals == [RUN_DIR] and verdict.run_dirs == []


@pytest.mark.parametrize(
    "changes",
    [
        [Change(f"{RUN_DIR}/report.json", "M", "100644")],
        [Change(f"{RUN_DIR}/raw/samples.csv.gz", "D", "100644")],
        [Change(f"{RUN_DIR}/notes/photos/late.jpg", "A", "100644")],
        [Change(f"{RUN_DIR}/WITHDRAWN.md", "M", "100644")],
        [Change(f"{RUN_DIR}/WITHDRAWN.md", "A", "100644"), Change(f"{RUN_DIR}/report.json", "M", "100644")],
    ],
)
def test_runs_on_main_are_permanent(changes):
    verdict = classify(changes, run_on_base=on_base)
    assert not verdict.ok
    assert any("results are permanent" in p for p in verdict.problems)


def test_withdrawn_md_in_a_new_run_is_rejected():
    verdict = classify(NEW_RUN + [Change(f"{RUN_DIR}/WITHDRAWN.md", "A")], run_on_base=not_on_base)
    assert any("already on main" in p for p in verdict.problems)


def test_no_changes_is_rejected():
    assert not classify([]).ok


def test_paths_only_mode_reads_a_list(monkeypatch, capsys):
    listing = "\n".join(c.path for c in NEW_RUN) + "\n"
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(listing.encode())))
    assert path_guard.main(["--paths-from", "-", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["run_dirs"] == [RUN_DIR] and data["kind"] == "results"


def test_limits_are_the_published_ones():
    assert path_guard.MAX_FILE_BYTES == 20 * 1024 * 1024
    assert path_guard.MAX_RUN_BYTES == 50 * 1024 * 1024


# ---------------------------------------------------------------- git mode, end to end


def add_run(repo: Repo, lab: str = "acme", run: str = RUN) -> str:
    base = f"results/{lab}/{run}"
    repo.write(f"{base}/report.json", {"test": "EV-03"})
    repo.write(f"{base}/envelope.json", {"envelope_version": "0.2"})
    repo.write(f"{base}/raw/accuracy.csv", "phase,label\n")
    return base


def test_git_mode_accepts_a_new_run_and_writes_outputs(repo: Repo, tmp_path, capsys):
    repo.write("labs/acme/lab.json", make_lab("acme", ["alice"], [make_key(1, "acme")]))
    repo.commit("register acme")
    repo.publish_base()
    repo.start_pr()
    run_dir = add_run(repo)
    repo.commit("submit")
    out = tmp_path / "github_output"
    assert path_guard.main(["--repo", str(repo.path), "--github-output", str(out)]) == 0
    values = dict(line.split("=", 1) for line in out.read_text().splitlines())
    assert values == {"kind": "results", "lab": "acme", "scope": "results/acme", "run_dirs": run_dir, "withdrawals": ""}
    assert "accepted" in capsys.readouterr().out


def test_git_mode_sees_both_sides_of_a_rename(repo: Repo, capsys):
    repo.start_pr()
    (repo.path / f"results/acme/{RUN}").mkdir(parents=True)
    repo.git("mv", "tools/path_guard.py", f"results/acme/{RUN}/report.json")
    repo.commit("move a tool into a run")
    assert path_guard.main(["--repo", str(repo.path)]) == 1
    assert "tools/path_guard.py: outside results/ and labs/" in capsys.readouterr().out


def test_git_mode_rejects_a_workflow_edit(repo: Repo, capsys):
    repo.start_pr()
    add_run(repo)
    repo.write(".github/workflows/verify.yml", "on: pull_request\n")
    repo.commit("sneak a workflow change in")
    assert path_guard.main(["--repo", str(repo.path)]) == 1
    assert ".github/workflows/verify.yml: outside results/ and labs/" in capsys.readouterr().out


def test_git_mode_rejects_editing_a_merged_run_and_accepts_a_withdrawal(repo: Repo, capsys):
    run_dir = add_run(repo)
    repo.commit("merged earlier")
    repo.publish_base()
    repo.start_pr()
    repo.write(f"{run_dir}/report.json", {"test": "EV-03", "edited": True})
    repo.commit("edit")
    assert path_guard.main(["--repo", str(repo.path)]) == 1
    assert "results are permanent" in capsys.readouterr().out

    repo.git("checkout", "-q", "main")
    repo.start_pr("withdraw")
    repo.write(f"{run_dir}/WITHDRAWN.md", "2026-10-20: wrong envelope; replaced by acme/ev03-20261021T100000Z-s7\n")
    repo.commit("withdraw")
    assert path_guard.main(["--repo", str(repo.path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["withdrawals"] == [run_dir]


def test_git_mode_rejects_an_empty_withdrawal_notice(repo: Repo):
    run_dir = add_run(repo)
    repo.commit("merged earlier")
    repo.publish_base()
    repo.start_pr()
    repo.write(f"{run_dir}/WITHDRAWN.md", "")
    repo.commit("withdraw without a reason")
    assert path_guard.main(["--repo", str(repo.path)]) == 1


def test_git_mode_rejects_a_committed_symlink(repo: Repo):
    repo.start_pr()
    run_dir = add_run(repo)
    (repo.path / run_dir / "raw" / "instrument").mkdir(parents=True)
    (repo.path / run_dir / "raw" / "instrument" / "calibration.json").symlink_to("../../../../../tools/path_guard.py")
    repo.commit("symlink")
    assert path_guard.main(["--repo", str(repo.path)]) == 1


def test_git_mode_rejects_oversize_files_and_runs(repo: Repo, monkeypatch, capsys):
    monkeypatch.setattr(path_guard, "MAX_FILE_BYTES", 1000)
    monkeypatch.setattr(path_guard, "MAX_RUN_BYTES", 2500)
    repo.start_pr()
    run_dir = add_run(repo)
    repo.write(f"{run_dir}/raw/samples.csv.gz", b"\x1f\x8b" + b"0" * 1200)
    repo.write(f"{run_dir}/raw/commands.csv.gz", b"\x1f\x8b" + b"0" * 900)
    repo.write(f"{run_dir}/raw/state.csv.gz", b"\x1f\x8b" + b"0" * 900)
    repo.commit("big")
    assert path_guard.main(["--repo", str(repo.path)]) == 1
    out = capsys.readouterr().out
    assert "the limit is 20 MB per file" in out and "the limit is 50 MB per run" in out


def test_git_mode_rejects_lfs_pointers_and_private_keys(repo: Repo, capsys):
    repo.start_pr()
    run_dir = add_run(repo)
    repo.write(f"{run_dir}/raw/samples.csv.gz", "version https://git-lfs.github.com/spec/v1\noid sha256:00\nsize 9\n")
    repo.write(f"{run_dir}/raw/instrument/key.json", {"kty": "OKP", "crv": "Ed25519", "x": "A" * 43, "d": "B" * 43})
    repo.commit("oops")
    assert path_guard.main(["--repo", str(repo.path)]) == 1
    out = capsys.readouterr().out
    assert "Git LFS pointer" in out and "private key" in out


def test_git_mode_without_a_base_cannot_run(tmp_path, capsys):
    bare = Repo(tmp_path / "nobase")
    bare.write("x", "y")
    bare.commit()
    assert path_guard.main(["--repo", str(bare.path)]) == 2
    assert "not found" in capsys.readouterr().err
