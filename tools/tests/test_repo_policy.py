"""The workflows and repository files keep the rules in POLICY.md and README.md."""
from __future__ import annotations

import json
import re

import pytest

from conftest import REPO_ROOT

WORKFLOWS = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
yaml = pytest.importorskip("yaml")


def load(name: str) -> dict:
    return yaml.safe_load((REPO_ROOT / ".github" / "workflows" / name).read_text())


def triggers(doc: dict) -> dict:
    return doc.get("on", doc.get(True))  # YAML 1.1 reads a bare `on` as True


def steps(doc: dict):
    for job_id, job in doc["jobs"].items():
        for step in job.get("steps", []):
            yield job_id, step


def test_the_three_workflows_exist():
    assert [w.name for w in WORKFLOWS] == ["pages.yml", "selftest.yml", "verify.yml"]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_pull_request_target_no_secrets_read_only_default(path):
    text = path.read_text()
    code = "\n".join(re.sub(r"(^|\s)#.*$", "", line) for line in text.splitlines())
    assert "pull_request_target" not in code and "workflow_run" not in code
    assert not re.search(r"\bsecrets\.\w", code)
    assert yaml.safe_load(text)["permissions"] == {"contents": "read"}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_actions_are_pinned_to_commits(path):
    for _, step in steps(yaml.safe_load(path.read_text())):
        if "uses" in step:
            assert SHA_PIN.match(step["uses"]), step["uses"]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_expressions_inside_shell_scripts(path):
    """Values reach scripts through env, never through ${{ }} pasted into a script."""
    for job_id, step in steps(yaml.safe_load(path.read_text())):
        assert "${{" not in step.get("run", ""), (job_id, step.get("name"))


def test_verify_runs_on_pull_request_with_the_required_jobs():
    doc = load("verify.yml")
    assert set(triggers(doc)) == {"pull_request"}
    jobs = doc["jobs"]
    assert set(jobs) == {"path-guard", "identity", "be-verify", "claim-lint", "submission-consistency"}
    for job in jobs.values():
        assert "permissions" not in job
    final = jobs["submission-consistency"]
    assert set(final["needs"]) == {"path-guard", "identity", "be-verify", "claim-lint"}
    assert final["if"] == "always()"
    assert jobs["identity"]["needs"] == "path-guard"
    assert set(jobs["be-verify"]["needs"]) == {"path-guard", "identity"}


def test_verify_runs_the_tools_from_main_and_pins_the_core():
    doc = load("verify.yml")
    scripts = "\n".join(step.get("run", "") for _, step in steps(doc))
    for call in re.findall(r"python3? (\S+\.py)", scripts):
        assert call.startswith('"$RUNNER_TEMP/base/tools/') or call.startswith('"$base/tools/'), call
    assert scripts.count('git worktree add --detach "$RUNNER_TEMP/base" origin/main') == 5
    assert "--require-hashes" in scripts and "--no-deps" in scripts and '"$base/kits.lock"' in scripts
    assert 'be" verify "$dir" --keys "$keys" --json' in scripts
    assert "github.event.pull_request.user.login" in (REPO_ROOT / ".github/workflows/verify.yml").read_text()


def test_every_checkout_keeps_no_credentials():
    for path in WORKFLOWS:
        for _, step in steps(yaml.safe_load(path.read_text())):
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False


def test_pages_builds_and_deploys_without_committing():
    doc = load("pages.yml")
    text = (REPO_ROOT / ".github/workflows/pages.yml").read_text()
    assert set(triggers(doc)) == {"push", "workflow_dispatch"}
    assert triggers(doc)["push"]["branches"] == ["main"]
    assert "git push" not in text and "git commit" not in text
    assert doc["jobs"]["deploy"]["permissions"] == {"pages": "write", "id-token": "write"}
    assert "permissions" not in doc["jobs"]["build"]
    uses = [step.get("uses", "") for _, step in steps(doc)]
    assert any(u.startswith("actions/upload-pages-artifact@") for u in uses)
    assert any(u.startswith("actions/deploy-pages@") for u in uses)
    assert "tools/build_table.py" in text


def test_selftest_runs_pytest_on_pull_requests_that_touch_tools():
    doc = load("selftest.yml")
    assert set(triggers(doc)) == {"pull_request"}
    assert "tools/**" in triggers(doc)["pull_request"]["paths"]
    assert "pytest -q tools/tests" in (REPO_ROOT / ".github/workflows/selftest.yml").read_text()


def test_codeowners_covers_the_protected_paths():
    owners = {}
    for line in (REPO_ROOT / ".github" / "CODEOWNERS").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            path, *who = line.split()
            owners[path] = who
    for path in ("/.github/", "/tools/", "/kits.lock", "/POLICY.md", "/CODEOWNERS", "/labs/"):
        assert owners.get(path) == ["@craigm26"], path
    assert not any(p.startswith("/results/") and p != "/results/README.md" for p in owners)


def test_licences_and_reuse():
    assert (REPO_ROOT / "LICENSE").read_text() == (REPO_ROOT / "LICENSES" / "Apache-2.0.txt").read_text()
    assert "Attribution 4.0 International" in (REPO_ROOT / "LICENSES" / "CC-BY-4.0.txt").read_text()
    try:
        import tomllib
    except ImportError:  # Python 3.10
        pytest.skip("tomllib needs Python 3.11")
    reuse = tomllib.loads((REPO_ROOT / "REUSE.toml").read_text())
    annotations = reuse["annotations"]
    assert annotations[0]["path"] == ["**"] and annotations[0]["SPDX-License-Identifier"] == "Apache-2.0"
    data = next(a for a in annotations if "results/**" in a["path"])
    assert "labs/**" in data["path"] and data["SPDX-License-Identifier"] == "CC-BY-4.0"
    assert annotations.index(data) > 0  # the last matching annotation wins in REUSE.toml


def test_kits_lock_and_template_parse():
    lock = json.loads((REPO_ROOT / "kits.lock").read_text())
    assert list(lock) == ["bounded-embodiment"]  # only the core is pinned
    template = json.loads((REPO_ROOT / "labs" / "_template" / "lab.json").read_text())
    assert {"name", "github_handles", "affiliation", "contact", "keys"} == set(template)


def test_the_readme_states_what_green_means():
    readme = (REPO_ROOT / "README.md").read_text()
    from _common import CONSISTENT_SENTENCE

    assert CONSISTENT_SENTENCE.format(core="X") in readme
    assert "maintains RCAN" in readme and "does not depend on RCAN" in readme
