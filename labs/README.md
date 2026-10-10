# Labs

Each lab has one file, `labs/<lab-id>/lab.json`. It says who may open pull requests for the lab and which keys may sign its reports. CI reads it from `main` only: never from a pull request, and never from a key embedded in a report.

## Lab ids

- Lower-case letters, digits and hyphens; 1 to 64 characters; starting and ending with a letter or a digit.
- `test-*` ids are dry-run labs. CI accepts them, the results table leaves them out, and the maintainer may remove them at any time.
- `_template` is reserved for the template.

## lab.json

Start from [`_template/lab.json`](_template/lab.json). Every field is required, and no others are allowed.

| Field | Meaning |
|---|---|
| `name` | The lab's name as it should appear in the table |
| `github_handles` | GitHub logins that may open pull requests for this lab. The author of every pull request for the lab must be one of them. |
| `affiliation` | Organisation, or `independent` |
| `contact` | A public contact: an email address or a URL |
| `keys` | One or more signing keys, each in key-0.1 form (below) |

Each entry in `keys` (key-0.1):

| Field | Meaning |
|---|---|
| `key_version` | `"0.1"` |
| `kid` | The key's RFC 7638 thumbprint: base64url, no padding. Signatures name it. |
| `alg` | `"Ed25519"`, the only signing key accepted in v0.1 |
| `public_jwk` | `{"kty": "OKP", "crv": "Ed25519", "x": "<base64url>"}`. The public part only, never `"d"` |
| `lab` | This lab's id |
| `contact` | Email address of the person responsible for the key |
| `added` | The date the key was added, `YYYY-MM-DD` |
| `revoked` | Optional: the date the key was retired, `YYYY-MM-DD`. Leave it out while the key is in use. |

`be keygen --out <private key file> --lab <lab-id> --contact <email>` prints a public key entry with these fields filled in; the core's `key-0.1` schema defines it. Add `revoked` only to retire a key.

## Registering a lab

1. Make a key with `be keygen --out <private key file> --lab <lab-id> --contact <email>`. Keep the private key file outside any repository, keep it secret, and never commit it.
2. Copy `_template/lab.json` to `labs/<lab-id>/lab.json`, fill it in, and put your public key entry in `keys`.
3. Open a pull request that changes nothing outside `labs/<lab-id>/`. CI checks:
   - the file's shape;
   - that each `kid` is the thumbprint of its `public_jwk`;
   - that no private key is included;
   - that no other lab on `main` lists the same `kid`;
   - that you, the pull request's author, are in `github_handles`.
4. The maintainer reviews the registration by hand and merges it. Identity is self-asserted (see [POLICY.md](../POLICY.md#identity)).

Submit results only after the registration is merged, because keys are read from `main`.

## Changing a lab

- Only people already listed in `github_handles` on `main` can change a lab's file. Changes go through the same review.
- You can add keys at any time.
- Never remove or edit a key, because earlier results must stay checkable. To retire a key, add `"revoked": "YYYY-MM-DD"`. A revoked key can't sign new submissions. A key's `contact` may change.
- If a private key leaks, revoke it at once and tell the maintainer.
- `name`, `affiliation`, `contact` and `github_handles` may change.
