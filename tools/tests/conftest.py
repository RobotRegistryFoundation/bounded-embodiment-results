"""Shared fixtures: throwaway git repositories with an origin/main, lab files and reports."""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

TOOLS = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import identity  # noqa: E402

RUN = "ev03-20261013T183000Z-s7"


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Keep the developer's own git configuration (signing, hooks, renames) out of the tests."""
    empty = tmp_path_factory.getbasetemp() / "empty-gitconfig"
    empty.touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("GITHUB_ACTOR", raising=False)


class Repo:
    """A git repository whose current main is recorded as origin/main (the base)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        cmd = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.org", "-c", "commit.gpgsign=false"]
        proc = subprocess.run([*cmd, *args], cwd=self.path, capture_output=True, text=True, check=True)
        return proc.stdout

    def write(self, rel: str, content: Any) -> Path:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, (dict, list)):
            content = json.dumps(content, indent=2) + "\n"
        if isinstance(content, str):
            content = content.encode("utf-8")
        target.write_bytes(content)
        return target

    def commit(self, message: str = "change") -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)

    def publish_base(self) -> None:
        """Record HEAD as origin/main, as actions/checkout with fetch-depth 0 leaves it."""
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")

    def start_pr(self, branch: str = "pr") -> None:
        self.git("checkout", "-q", "-b", branch)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    r = Repo(tmp_path / "repo")
    r.write("README.md", "base\n")
    r.write("tools/path_guard.py", "# base copy\n")
    r.commit("base")
    r.publish_base()
    return r


def make_key(seed: int, lab: str, revoked: str | None = None, contact: str = "keys@example.org") -> dict:
    """A key-0.1 entry with a well-formed (not necessarily on-curve) 32-byte public key."""
    x = base64.urlsafe_b64encode(bytes([seed]) * 32).rstrip(b"=").decode()
    jwk = {"kty": "OKP", "crv": "Ed25519", "x": x}
    entry = {
        "key_version": "0.1",
        "kid": identity.thumbprint(jwk),
        "alg": "Ed25519",
        "public_jwk": jwk,
        "lab": lab,
        "contact": contact,
        "added": "2026-10-10",
    }
    if revoked:
        entry["revoked"] = revoked
    return entry


def make_lab(lab: str, handles: list[str], keys: list[dict], name: str | None = None) -> dict:
    return {
        "name": name or f"{lab} lab",
        "github_handles": handles,
        "affiliation": "Example University",
        "contact": f"{lab}@example.org",
        "keys": keys,
    }


def make_report(**overrides: Any) -> dict:
    """The report fields the repo tools read; everything else is the core's business."""
    report: dict[str, Any] = {
        "test": "EV-03",
        "role": "test",
        "method": {"id": "ev03-tool-point", "version": "0.1", "text_sha256": "sha256:" + "0" * 64},
        "run": {"mode": "full", "started_at": "2026-10-13T18:30:00Z", "ended_at": "2026-10-13T18:40:00Z"},
        "system_under_test": {"machine": {"make": "Example", "model": "withheld", "class": "arm"},
                              "gate": {"name": "reference-gate", "version": "0.1.0", "placement": "host"}},
        "instruments": [{"kind": "optical_marker_tracker", "external": True}],
        "provenance": {"core_version": "0.1.0"},
        "envelope": {"hash": "sha256:" + "a" * 64},
        "verdict": {"result": "FAIL", "reasons": [{"code": "POSITION_EXCURSION", "detail": "3 samples outside"}]},
        "statements": {"not_certification": True, "thresholds_are_illustrative": True},
        "license": "CC-BY-4.0",
        "rights_affirmed": True,
        "signer": {"name": "A. Person", "organization": "Example", "role": "operator", "kid": "k" * 43},
        "signature": [{"alg": "Ed25519", "kid": "k" * 43, "sig": "s" * 86}],
    }
    for dotted, value in overrides.items():
        target = report
        parts = dotted.split("__")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    return report
