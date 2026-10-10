#!/usr/bin/env python3
"""Identity: who may open pull requests for a lab, and which keys may sign its reports.

For a results pull request it reads labs/<lab>/lab.json from the base branch
(git show origin/main:labs/<lab>/lab.json), never from the pull request and never
from a report. It checks that the pull request's author is in github_handles and
writes the keyring {kid: public_jwk} of the lab's unrevoked keys for
`be verify --keys`.

For a lab pull request it checks the lab.json in the pull request: its shape,
that each kid is the RFC 7638 thumbprint of its public key, that no private key
is included, and that no other lab on main uses the same kid. For a lab already
on main, the author must be listed in the file on main, and keys on main must
stay, unchanged except for an added "revoked" date. For a new lab, the author
must be listed in the new file; identity is self-asserted and the maintainer
reviews every registration by hand.

  python3 tools/identity.py results/<lab> --author <login> --keyring-out keyring.json
  python3 tools/identity.py labs/<lab> --author <login>

--author defaults to $GITHUB_ACTOR. Exit status: 0 accepted, 1 rejected, 2 could not run.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from _common import (
    DEFAULT_BASE,
    GITHUB_LOGIN_RE,
    LAB_ID_RE,
    ToolError,
    git,
    git_show,
    loads_strict,
    read_json,
    require_base,
    write_github_output,
)

LAB_FIELDS = frozenset({"name", "github_handles", "affiliation", "contact", "keys"})
KEY_FIELDS = frozenset({"key_version", "kid", "alg", "public_jwk", "lab", "contact", "added"})
KEY_OPTIONAL = frozenset({"revoked"})
# Fields of a key that may never change once the key is on main ("contact" may).
KEY_FIXED = ("key_version", "kid", "alg", "public_jwk", "lab", "added")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# As in the core's key-0.1 schema: 32 bytes in canonical base64url (43 characters).
X_RE = re.compile(r"^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def b64url_decode(text: str) -> bytes:
    if not B64URL_RE.match(text):
        raise ValueError("not base64url")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except binascii.Error as exc:
        raise ValueError(str(exc)) from None


def thumbprint(public_jwk: dict[str, Any]) -> str:
    """RFC 7638 JWK thumbprint of an OKP key: sha256 over {"crv","kty","x"}, base64url, no padding."""
    members = {"crv": public_jwk["crv"], "kty": public_jwk["kty"], "x": public_jwk["x"]}
    canonical = json.dumps(members, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _is_date(value: Any) -> bool:
    if not isinstance(value, str) or not DATE_RE.match(value):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _key_problems(key: Any, where: str, lab: str) -> list[str]:
    if not isinstance(key, dict):
        return [f"{where}: must be an object"]
    problems: list[str] = []
    missing = KEY_FIELDS - set(key)
    unknown = set(key) - KEY_FIELDS - KEY_OPTIONAL
    if missing:
        problems.append(f"{where}: missing {', '.join(sorted(missing))}")
    if unknown:
        problems.append(f"{where}: unknown field(s) {', '.join(sorted(unknown))}")
    if "key_version" in key and key["key_version"] != "0.1":
        problems.append(f'{where}.key_version: must be "0.1"')
    if "alg" in key and key["alg"] != "Ed25519":
        problems.append(f'{where}.alg: must be "Ed25519" (the only signing key accepted in v0.1)')
    if "lab" in key and key["lab"] != lab:
        problems.append(f"{where}.lab: must be the lab id {lab!r}")
    if "contact" in key and not (isinstance(key["contact"], str) and EMAIL_RE.match(key["contact"])
                                 and len(key["contact"]) <= 254):
        problems.append(f"{where}.contact: must be an email address")
    if "added" in key and not _is_date(key["added"]):
        problems.append(f"{where}.added: must be a date, YYYY-MM-DD")
    if "revoked" in key:
        if not _is_date(key["revoked"]):
            problems.append(f'{where}.revoked: must be a date, YYYY-MM-DD (leave it out if the key is in use)')
        elif _is_date(key.get("added")) and key["revoked"] < key["added"]:
            problems.append(f"{where}.revoked: earlier than added")
    jwk = key.get("public_jwk")
    jwk_ok = False
    if "public_jwk" in key:
        if not isinstance(jwk, dict):
            problems.append(f"{where}.public_jwk: must be an object")
        elif "d" in jwk:
            problems.append(
                f'{where}.public_jwk: contains a private key ("d"). Remove it, treat the key as '
                "compromised and make a new one"
            )
        elif set(jwk) != {"kty", "crv", "x"}:
            problems.append(f'{where}.public_jwk: must have exactly "kty", "crv" and "x"')
        elif jwk["kty"] != "OKP" or jwk["crv"] != "Ed25519":
            problems.append(f'{where}.public_jwk: must be "kty": "OKP", "crv": "Ed25519"')
        else:
            try:
                raw = b64url_decode(jwk["x"]) if isinstance(jwk["x"], str) and X_RE.match(jwk["x"]) else b""
            except ValueError:
                raw = b""
            if len(raw) != 32:
                problems.append(f'{where}.public_jwk.x: must be a 32-byte Ed25519 public key in base64url')
            else:
                jwk_ok = True
    if "kid" in key:
        if not isinstance(key["kid"], str):
            problems.append(f"{where}.kid: must be a string")
        elif jwk_ok and key["kid"] != thumbprint(jwk):
            problems.append(f"{where}.kid: is not the RFC 7638 thumbprint of public_jwk ({thumbprint(jwk)})")
    return problems


def validate_lab(doc: Any, lab: str) -> list[str]:
    """Problems with a lab.json document for lab id `lab` (empty list if none)."""
    if not isinstance(doc, dict):
        return ["must be a JSON object"]
    problems: list[str] = []
    missing = LAB_FIELDS - set(doc)
    unknown = set(doc) - LAB_FIELDS
    if missing:
        problems.append(f"missing field(s): {', '.join(sorted(missing))}")
    if unknown:
        problems.append(f"unknown field(s): {', '.join(sorted(unknown))}")
    for name in ("name", "affiliation", "contact"):
        if name in doc and not _nonempty_str(doc[name]):
            problems.append(f"{name}: must be a non-empty string")
    handles = doc.get("github_handles")
    if "github_handles" in doc:
        if not isinstance(handles, list) or not handles:
            problems.append("github_handles: must be a non-empty list of GitHub logins")
        else:
            seen: set[str] = set()
            for i, handle in enumerate(handles):
                if not isinstance(handle, str) or not GITHUB_LOGIN_RE.match(handle):
                    problems.append(f"github_handles[{i}]: not a GitHub login")
                elif handle.lower() in seen:
                    problems.append(f"github_handles[{i}]: listed twice")
                else:
                    seen.add(handle.lower())
    keys = doc.get("keys")
    if "keys" in doc:
        if not isinstance(keys, list) or not keys:
            problems.append("keys: must be a non-empty list")
        else:
            kids: set[str] = set()
            for i, key in enumerate(keys):
                problems += _key_problems(key, f"keys[{i}]", lab)
                kid = key.get("kid") if isinstance(key, dict) else None
                if isinstance(kid, str):
                    if kid in kids:
                        problems.append(f"keys[{i}].kid: listed twice")
                    kids.add(kid)
    return problems


def author_listed(author: str, doc: Any) -> bool:
    handles = doc.get("github_handles") if isinstance(doc, dict) else None
    if not isinstance(handles, list):
        return False
    return author.lower() in {h.lower() for h in handles if isinstance(h, str)}


def keyring_of(doc: dict[str, Any]) -> dict[str, dict[str, str]]:
    """{kid: public_jwk} for the unrevoked keys of a valid lab.json."""
    ring: dict[str, dict[str, str]] = {}
    for key in doc["keys"]:
        if "revoked" in key:
            continue
        jwk = key["public_jwk"]
        ring[key["kid"]] = {"crv": jwk["crv"], "kty": jwk["kty"], "x": jwk["x"]}
    return ring


def kids_used_elsewhere(kids: set[str], lab: str, base: str, repo: Path) -> list[str]:
    """Problems for any kid in `kids` that another lab on `base` also lists."""
    listing = git(["ls-tree", "-r", "--name-only", base, "--", "labs/"], repo).decode("utf-8", "replace")
    problems: list[str] = []
    for path in sorted(listing.splitlines()):
        parts = path.split("/")
        if len(parts) != 3 or parts[2] != "lab.json" or parts[1] == lab or not LAB_ID_RE.match(parts[1]):
            continue
        raw = git_show(base, path, repo)
        if raw is None:
            continue
        other = loads_strict(raw, f"{base}:{path}")
        keys = other.get("keys") if isinstance(other, dict) else None
        for key in keys if isinstance(keys, list) else []:
            kid = key.get("kid") if isinstance(key, dict) else None
            if isinstance(kid, str) and kid in kids:
                problems.append(f"key {kid} is already listed by lab {parts[1]!r}; a key belongs to one lab")
    return problems


def check_results(lab: str, author: str, base: str, repo: Path) -> tuple[list[str], dict[str, dict[str, str]]]:
    where = f"{base}:labs/{lab}/lab.json"
    raw = git_show(base, f"labs/{lab}/lab.json", repo)
    if raw is None:
        return [
            f"lab {lab!r} is not registered on {base}: register it first, with a pull request "
            f"that changes only labs/{lab}/"
        ], {}
    doc = loads_strict(raw, where)
    shape = validate_lab(doc, lab)
    if shape:
        return [f"{where}: {p} (the maintainer must fix this file)" for p in shape], {}
    problems: list[str] = []
    if not author_listed(author, doc):
        problems.append(f"the pull request's author {author!r} is not in github_handles of {where}")
    ring = keyring_of(doc)
    if not ring:
        problems.append(f"{where} has no unrevoked key")
    problems += kids_used_elsewhere(set(ring), lab, base, repo)
    return problems, ring


def check_lab_change(lab: str, author: str, base: str, repo: Path) -> tuple[list[str], str]:
    head_path = repo / "labs" / lab / "lab.json"
    if not head_path.is_file() or head_path.is_symlink():
        return [
            f"labs/{lab}/lab.json is missing from the pull request; a lab can't be removed by "
            "pull request (ask the maintainer)"
        ], ""
    head = read_json(head_path)
    problems = [f"labs/{lab}/lab.json: {p}" for p in validate_lab(head, lab)]
    raw = git_show(base, f"labs/{lab}/lab.json", repo)
    if raw is None:
        status = "new"
        if not author_listed(author, head):
            problems.append(f"the pull request's author {author!r} is not in the new file's github_handles")
    else:
        status = "existing"
        old = loads_strict(raw, f"{base}:labs/{lab}/lab.json")
        if not author_listed(author, old):
            problems.append(
                f"the pull request's author {author!r} is not in github_handles of labs/{lab}/lab.json on "
                f"{base}; only people already listed can change a lab"
            )
        problems += key_history_problems(old, head)
    head_keys = head.get("keys") if isinstance(head, dict) else None
    kids = {k["kid"] for k in head_keys or [] if isinstance(k, dict) and isinstance(k.get("kid"), str)}
    problems += kids_used_elsewhere(kids, lab, base, repo)
    return problems, status


def key_history_problems(old: Any, new: Any) -> list[str]:
    """Keys on main stay listed and unchanged; a revocation, once made, stays."""

    def by_kid(doc: Any) -> dict[str, dict[str, Any]]:
        keys = doc.get("keys") if isinstance(doc, dict) else None
        return {k["kid"]: k for k in keys or [] if isinstance(k, dict) and isinstance(k.get("kid"), str)}

    problems: list[str] = []
    new_keys = by_kid(new)
    for kid, old_key in by_kid(old).items():
        new_key = new_keys.get(kid)
        if new_key is None:
            problems.append(
                f'key {kid} was removed; keys stay listed so earlier results can still be checked '
                f'(retire a key with "revoked": "YYYY-MM-DD")'
            )
            continue
        changed = [f for f in KEY_FIXED if new_key.get(f) != old_key.get(f)]
        if changed:
            problems.append(f"key {kid}: {', '.join(changed)} changed; a listed key can't be edited, only revoked")
        if "revoked" in old_key and new_key.get("revoked") != old_key["revoked"]:
            problems.append(f"key {kid}: a revocation can't be changed or removed")
    return problems


def parse_scope(scope: str) -> tuple[str, str]:
    parts = scope.strip("/").split("/")
    if len(parts) < 2 or parts[0] not in ("results", "labs") or not LAB_ID_RE.match(parts[1]):
        raise ToolError(f"{scope!r}: expected results/<lab> or labs/<lab>")
    return ("results" if parts[0] == "results" else "lab"), parts[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("scope", help="results/<lab> or labs/<lab> (or any path inside one)")
    parser.add_argument("--author", help="GitHub login of the pull request's author (default: $GITHUB_ACTOR)")
    parser.add_argument("--base", default=DEFAULT_BASE, help="base ref (default: origin/main)")
    parser.add_argument("--repo", default=".", help="checkout of the pull request (default: .)")
    parser.add_argument("--keyring-out", metavar="FILE", help="write {kid: public_jwk} here (results only)")
    parser.add_argument("--github-output", metavar="FILE", help="append keyring and lab_status")
    args = parser.parse_args(argv)
    repo = Path(args.repo)
    author = args.author or os.environ.get("GITHUB_ACTOR", "")
    try:
        kind, lab = parse_scope(args.scope)
        if not GITHUB_LOGIN_RE.match(author):
            raise ToolError("no pull request author: pass --author or set GITHUB_ACTOR")
        require_base(args.base, repo)
        if kind == "results":
            problems, ring = check_results(lab, author, args.base, repo)
            status = "existing"
        else:
            problems, status = check_lab_change(lab, author, args.base, repo)
            ring = {}
    except ToolError as exc:
        print(f"identity: could not run: {exc}", file=sys.stderr)
        return 2
    if problems:
        print(f'identity: rejected for lab "{lab}".')
        for problem in problems:
            print(f"  - {problem}")
        return 1
    compact = json.dumps(ring, sort_keys=True, separators=(",", ":"))
    if kind == "results":
        print(
            f'identity: accepted. {author} is listed for lab "{lab}" on {args.base}; '
            f"keyring has {len(ring)} key(s): {', '.join(sorted(ring))}"
        )
        if args.keyring_out:
            Path(args.keyring_out).write_text(compact, encoding="utf-8")
    elif status == "new":
        print(
            f'identity: accepted. New lab "{lab}": the file is well formed and {author} is listed in it. '
            "Identity is self-asserted; the maintainer reviews every registration by hand."
        )
    else:
        print(
            f'identity: accepted. Change to lab "{lab}": {author} is listed on {args.base}, and the keys '
            "on main are kept unchanged."
        )
    if args.github_output:
        values = {"lab_status": status}
        if kind == "results":
            values["keyring"] = compact
        write_github_output(args.github_output, values)
    return 0


if __name__ == "__main__":
    sys.exit(main())
