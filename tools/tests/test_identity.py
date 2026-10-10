from __future__ import annotations

import json

import pytest

import identity
from conftest import RUN, Repo, make_key, make_lab


def test_thumbprint_matches_rfc_8037_appendix_a3():
    jwk = {"kty": "OKP", "crv": "Ed25519", "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"}
    assert identity.thumbprint(jwk) == "kPrK_qmxVWaYVA9wwBF6Iuo3vVzz7TxHCTwXBygrS4k"


def test_a_well_formed_lab_file_has_no_problems():
    assert identity.validate_lab(make_lab("acme", ["alice"], [make_key(1, "acme")]), "acme") == []


def test_the_template_is_well_formed_apart_from_its_placeholders():
    from conftest import REPO_ROOT

    template = json.loads((REPO_ROOT / "labs/_template/lab.json").read_text())
    assert set(template) == identity.LAB_FIELDS
    assert set(template["keys"][0]) == identity.KEY_FIELDS
    problems = identity.validate_lab(template, "example-lab")
    assert all(("public_jwk.x" in p) or (".lab:" in p) for p in problems), problems


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda d: d["keys"][0].update(kid="A" * 43), "not the RFC 7638 thumbprint"),
        (lambda d: d["keys"][0]["public_jwk"].update(d="B" * 43), "contains a private key"),
        (lambda d: d["keys"][0].update(lab="other"), "must be the lab id"),
        (lambda d: d["keys"][0].update(added="10/10/2026"), "added: must be a date"),
        (lambda d: d["keys"][0].update(revoked="2026-01-01"), "earlier than added"),
        (lambda d: d["keys"][0].update(revoked=None), "revoked: must be a date"),
        (lambda d: d["keys"][0].update(alg="ES256"), "must be \"Ed25519\""),
        (lambda d: d["keys"][0].update(contact="https://example.org"), "must be an email address"),
        (lambda d: d["keys"][0]["public_jwk"].update(x="11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURp"), "32-byte"),
        (lambda d: d["keys"][0].update(level="BE-2"), "unknown field"),
        (lambda d: d.update(certified=True), "unknown field"),
        (lambda d: d.update(github_handles=["alice", "Alice"]), "listed twice"),
        (lambda d: d.update(github_handles=[]), "non-empty list"),
        (lambda d: d.update(keys=[d["keys"][0], d["keys"][0]]), "listed twice"),
        (lambda d: d.pop("contact"), "missing field"),
    ],
)
def test_lab_file_problems(mutate, expected):
    doc = make_lab("acme", ["alice"], [make_key(1, "acme")])
    mutate(doc)
    problems = identity.validate_lab(doc, "acme")
    assert any(expected in p for p in problems), problems


# ---------------------------------------------------------------- results pull requests


@pytest.fixture
def registered(repo: Repo) -> Repo:
    repo.write(
        "labs/acme/lab.json",
        make_lab("acme", ["Alice", "bob"], [make_key(1, "acme"), make_key(2, "acme", revoked="2026-10-11")]),
    )
    repo.write("labs/other/lab.json", make_lab("other", ["carol"], [make_key(3, "other")]))
    repo.commit("register labs")
    repo.publish_base()
    repo.start_pr()
    return repo


def test_results_keyring_holds_the_lab_s_unrevoked_keys_from_main(registered: Repo, tmp_path):
    keyring = tmp_path / "keyring.json"
    out = tmp_path / "github_output"
    code = identity.main(
        ["results/acme", "--repo", str(registered.path), "--author", "alice",
         "--keyring-out", str(keyring), "--github-output", str(out)]
    )
    assert code == 0
    ring = json.loads(keyring.read_text())
    key = make_key(1, "acme")
    assert ring == {key["kid"]: key["public_jwk"]}
    lines = out.read_text().splitlines()
    assert f"keyring={keyring.read_text()}" in lines and "lab_status=existing" in lines


def test_results_author_is_matched_without_case_and_falls_back_to_github_actor(registered: Repo, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTOR", "ALICE")
    assert identity.main(["results/acme", "--repo", str(registered.path)]) == 0


def test_results_author_must_be_listed(registered: Repo, capsys):
    assert identity.main(["results/acme", "--repo", str(registered.path), "--author", "mallory"]) == 1
    assert "not in github_handles" in capsys.readouterr().out


def test_results_for_an_unregistered_lab_are_refused(registered: Repo, capsys):
    assert identity.main(["results/nobody", "--repo", str(registered.path), "--author", "alice"]) == 1
    assert "is not registered" in capsys.readouterr().out


def test_keys_are_read_from_main_never_from_the_pull_request(registered: Repo, tmp_path):
    """A pull request that edits its lab file to add its own key gets the keyring from main."""
    doc = json.loads((registered.path / "labs/acme/lab.json").read_text())
    doc["keys"].append(make_key(9, "acme"))
    doc["github_handles"].append("mallory")
    registered.write("labs/acme/lab.json", doc)
    registered.write(f"results/acme/{RUN}/report.json", {"signature": [{"kid": make_key(9, "acme")["kid"]}]})
    registered.commit("swap in my own key")
    keyring = tmp_path / "keyring.json"
    assert identity.main(["results/acme", "--repo", str(registered.path), "--author", "mallory"]) == 1
    assert identity.main(
        ["results/acme", "--repo", str(registered.path), "--author", "alice", "--keyring-out", str(keyring)]
    ) == 0
    assert make_key(9, "acme")["kid"] not in json.loads(keyring.read_text())


def test_a_key_listed_by_two_labs_is_refused(repo: Repo, capsys):
    shared = make_key(5, "acme")
    repo.write("labs/acme/lab.json", make_lab("acme", ["alice"], [shared]))
    stolen = dict(shared, lab="copycat")
    repo.write("labs/copycat/lab.json", make_lab("copycat", ["mallory"], [stolen]))
    repo.commit("both on main")
    repo.publish_base()
    assert identity.main(["results/copycat", "--repo", str(repo.path), "--author", "mallory"]) == 1
    assert "already listed by lab 'acme'" in capsys.readouterr().out


# ---------------------------------------------------------------- lab pull requests


def test_new_lab_registration(registered: Repo, capsys):
    registered.write("labs/newlab/lab.json", make_lab("newlab", ["dave"], [make_key(7, "newlab")]))
    registered.commit("register")
    assert identity.main(["labs/newlab", "--repo", str(registered.path), "--author", "dave"]) == 0
    assert "self-asserted" in capsys.readouterr().out
    assert identity.main(["labs/newlab", "--repo", str(registered.path), "--author", "erin"]) == 1


def test_new_lab_cannot_reuse_another_lab_s_key(registered: Repo):
    copied = dict(make_key(3, "other"), lab="newlab")
    registered.write("labs/newlab/lab.json", make_lab("newlab", ["dave"], [copied]))
    registered.commit("register with a copied key")
    assert identity.main(["labs/newlab", "--repo", str(registered.path), "--author", "dave"]) == 1


def test_only_people_listed_on_main_can_change_a_lab(registered: Repo):
    doc = json.loads((registered.path / "labs/acme/lab.json").read_text())
    doc["github_handles"].append("mallory")
    registered.write("labs/acme/lab.json", doc)
    registered.commit("add myself")
    assert identity.main(["labs/acme", "--repo", str(registered.path), "--author", "mallory"]) == 1
    assert identity.main(["labs/acme", "--repo", str(registered.path), "--author", "bob"]) == 0


def test_adding_and_revoking_keys_is_allowed(registered: Repo):
    doc = json.loads((registered.path / "labs/acme/lab.json").read_text())
    doc["keys"][0]["revoked"] = "2026-10-12"
    doc["keys"].append(make_key(8, "acme"))
    registered.write("labs/acme/lab.json", doc)
    registered.commit("rotate")
    assert identity.main(["labs/acme", "--repo", str(registered.path), "--author", "alice"]) == 0


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda d: d["keys"].pop(0), "was removed"),
        (lambda d: d["keys"][1].pop("revoked"), "revocation can't be changed"),
        (lambda d: d["keys"][0].update(added="2026-10-09"), "can't be edited"),
    ],
)
def test_keys_on_main_stay_and_stay_unchanged(registered: Repo, capsys, mutate, expected):
    doc = json.loads((registered.path / "labs/acme/lab.json").read_text())
    mutate(doc)
    registered.write("labs/acme/lab.json", doc)
    registered.commit("rewrite history")
    assert identity.main(["labs/acme", "--repo", str(registered.path), "--author", "alice"]) == 1
    assert expected in capsys.readouterr().out


def test_a_lab_cannot_be_deleted_by_pull_request(registered: Repo, capsys):
    registered.git("rm", "-q", "labs/acme/lab.json")
    registered.commit("delete")
    assert identity.main(["labs/acme", "--repo", str(registered.path), "--author", "alice"]) == 1
    assert "can't be removed" in capsys.readouterr().out


def test_bad_scope_or_missing_author_cannot_run(registered: Repo):
    assert identity.main(["tools", "--repo", str(registered.path), "--author", "alice"]) == 2
    assert identity.main(["results/acme", "--repo", str(registered.path)]) == 2
