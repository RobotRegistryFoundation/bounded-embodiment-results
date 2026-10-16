# Bounded Embodiment results

Signed reports from labs that ran a [Bounded Embodiment](https://github.com/RobotRegistryFoundation/bounded-embodiment) test on their own machines, submitted here by pull request. The results table is built from `main` and published at <https://robotregistryfoundation.org/bounded-embodiment/results/>.

Every result is **self-run**: the lab that submitted it ran the test, measured it and signed the report. A result counts as reproduced only when a different lab reproduces it. The rules are in [POLICY.md](POLICY.md).

## What a green check means

The check that matters on a pull request is `submission-consistency`. Green means **consistent**:

> Consistent: signed by a registered key, files match, and the verdict follows from the submitted data under core X. CI cannot check that the data came from the robot described, or that the envelope is adequate.

Green does not mean the robot passed. The verdict (PASS, FAIL or INCONCLUSIVE) is worked out by the core package from the raw data, and a FAIL report gets a green check exactly as a PASS report does. Only runs measured by an external instrument can come out PASS; the core enforces that, and this repository only displays it.

What CI does not check, and cannot:

- that the data came from the robot, gate and instrument the report describes;
- that the envelope is adequate for the machine or its task;
- that a lab is who it says it is. Lab identity is self-asserted.

The counterweights are signatures checked against keys already on `main`, setup photos, control runs, the core's flag for an instrument track that only copies the robot's own reported state, and reproduction by a different lab.

These results must not be cited as evidence for certification, accreditation, a registry tier or any regulatory claim (see [POLICY.md](POLICY.md)).

## How to submit a result

1. **Make a signing key.** `pip install bounded-embodiment`, then:
   ```sh
   be keygen --out <path outside any repository>/<lab-id>.key.json --lab <lab-id> --contact you@example.org
   ```
   It writes your private key with file mode 0600 (keep it secret and never commit it) and prints the public key entry (key-0.1) for your lab file.
2. **Register your lab by pull request.** Copy `labs/_template/lab.json` to `labs/<lab-id>/lab.json`, fill it in, add your public key entry, and open a pull request that changes nothing outside `labs/<lab-id>/`. The maintainer reviews every registration by hand; see [labs/README.md](labs/README.md). Wait for the merge, because keys are read from `main`.
3. **Run the test with the kit.** Install `bounded-embodiment-kit` and follow the [lab guide](https://github.com/RobotRegistryFoundation/bounded-embodiment/blob/main/docs/lab-guide.md): the instrument check, a control run, a shakedown, then the full run. `be report` packages the run (it scrubs hostnames, usernames and paths, gzips the CSVs and adds notes and photos) and signs it.
4. **Run `be verify` locally.** It is the same check CI runs. Build your keyring from `main` with this repository's identity tool, then check the run:
   ```sh
   git fetch origin main
   python3 tools/identity.py results/<lab-id> --author <your-github-login> --keyring-out keyring.json
   be verify <run_dir> --keys keyring.json --json
   ```
5. **Run `be submit`.** It runs the core check again, enforces the size limits, refuses simulations unless their role is `rehearsal`, refuses unreleased core versions, copies the run into `results/<lab-id>/<run>/` in your clone of this repository, and prints the `gh pr create` command.
6. **Open the pull request.** Change nothing outside `results/<lab-id>/`. CI runs the path guard, the identity check, `be verify` under the core version your report names (installed from PyPI and checked against the hash in `kits.lock`) and the claim-language lint. Workflows from first-time contributors wait until the maintainer allows them to run.
7. **After the merge, the table updates.** robotregistryfoundation.org runs `tools/build_table.py` on `main` each time the site is built, at least hourly. Nothing is committed back.

To withdraw a result later, add `WITHDRAWN.md` to its run directory by pull request; see [POLICY.md](POLICY.md#permanence-and-withdrawal).

## The table

One row per run: the test, the verdict, the run's role (test, control or rehearsal), the lab, its identity level, the instrument class, whether a control run for the same setup failed, whether a different lab has reproduced it, and the core and method versions. Results from any of the maintainer's projects are labelled "maintainer's own", and all of those projects count as one lab.

There is no level column. A self-run test doesn't establish a level, and the self-declared `declared_level` an envelope may carry is never shown. Dry-run labs (`test-*`) are left out. The same rows are published as JSON at <https://robotregistryfoundation.org/bounded-embodiment/results.json>. `tools/build_table.py` builds both; robotregistryfoundation.org runs it on `main` when it builds the site.

## kits.lock

`kits.lock` lists the released versions of the core package, `bounded-embodiment`, that reports may name in `provenance.core_version`. It is JSON, so it has no comments; its format is:

```json
{
  "bounded-embodiment": {
    "0.1.0": {
      "wheel_sha256": "<sha256 of bounded_embodiment-0.1.0-py3-none-any.whl as published on PyPI: 64 lower-case hex digits>",
      "released": "YYYY-MM-DD"
    }
  }
}
```

- Each key is a released version number: `X.Y.Z`, optionally with `aN`, `bN`, `rcN` or `.postN`. Development and local versions are refused.
- Each entry has exactly `wheel_sha256` and `released`.
- The maintainer adds an entry in the pull request that follows each release. Entries are never edited or removed, so earlier results can always be checked again under the core that produced them.
- A report naming a version that isn't listed is refused (`tools/pin_core.py`).
- Only the core wheel is pinned. CI installs it with `pip --require-hashes --no-deps`, then installs its dependencies from PyPI within the ranges the core declares.

## What CI runs

All checking of reports lives in the pinned core wheel (`be verify`); the workflow YAML holds no checking logic. The tools in `tools/` use only the Python standard library:

| Tool | What it does |
|---|---|
| `path_guard.py` | Accepts a pull request only if every changed path is inside `results/<one lab>/` (new runs, or `WITHDRAWN.md` for a run already on `main`) or inside `labs/<one lab>/`. Refuses edits to merged runs, symbolic links, Git LFS pointers, private keys and oversized files. |
| `identity.py` | Reads `labs/<lab>/lab.json` from `main` (`git show origin/main:...`), never from the pull request or a report. Checks the pull request's author is listed and writes the keyring of unrevoked keys for `be verify --keys`. |
| `pin_core.py` | Turns the core version a report names into a hash-pinned requirements file from `kits.lock`, or refuses it. |
| `claim_lint.py` | Flags the words in `claim-words.txt` in `report.json` string values and `NOTES.md`. |
| `summary.py` | Writes the `submission-consistency` summary. |
| `build_table.py` | Builds `site/index.html` and `site/results.json` from `main`. |

`tools/tests` holds their tests: `python3 -m pytest -q tools/tests`.

## How this repository is protected

- Workflows run on `pull_request`, never `pull_request_target`, with `permissions: contents: read` and no secrets. This repository publishes nothing itself: the table is built by the robotregistryfoundation.org site from `main`.
- A `pull_request` run uses the workflow file from the pull request itself, so CI cannot protect the CI. Review does. [`.github/CODEOWNERS`](.github/CODEOWNERS) requests the maintainer's review for `.github/`, `tools/`, `kits.lock`, `POLICY.md`, `CODEOWNERS` and `labs/`. Branch protection is not enabled on `main` yet, so nothing in GitHub enforces that review or the `submission-consistency` check. What holds today is practice: the maintainer reviews every pull request before merging, merges only when the checks pass, and merges a result pull request only when every changed path is inside `results/<lab>/`.
- Every verify job runs the tools, `kits.lock` and the claim-word list from `main`, not from the pull request. A pull request that edits only `tools/` therefore can't change what is checked. One that edits a workflow can, which is why workflow changes need the maintainer's review.
- Pull requests that change anything outside `results/` and `labs/` fail the path guard by design. The maintainer reviews and merges them by hand.

## Licences

- **Data**, everything under `results/` and `labs/`: [CC BY 4.0](LICENSES/CC-BY-4.0.txt). Each lab grants it through the signed `license` and `rights_affirmed` fields of its report. Attribute a result to the lab that submitted it and link to its run directory.
- **Tooling and documentation**, everything else: [Apache-2.0](LICENSE).

[`REUSE.toml`](REUSE.toml) records this per path.

## Disclosure

The method's maintainer, Craig Merry, also maintains RCAN (the Robot Communication and Addressing Network protocol) and the Robot Registry Foundation registry. The method does not depend on RCAN: running a test or submitting a result needs no RCAN software, identifier or registration, and the core package has no RCAN dependency. The maintainer's own results are labelled "maintainer's own" in the table.
