# Policy

This repository holds self-run Bounded Embodiment test results: signed reports and their raw data, submitted by labs by pull request. CI checks each submission for **consistency**: it is signed by a key registered for the lab, its files match the hashes in the report, and its verdict follows from the submitted data under the core version it names. Consistent is not passed. A FAIL report is consistent when its data supports FAIL, and it is listed the same way as any other.

CI cannot check that the data came from the robot described, or that the envelope is adequate.

## Not certification evidence

Results here are not certification evidence and must not be cited as such. Don't cite them as evidence for certification, accreditation, conformity assessment, a registry tier (including any tier in the Robot Registry Foundation registry), a procurement qualification or a regulatory filing.

A result may be cited for what it is: a self-run measurement, with illustrative thresholds, whose verdict follows from its submitted data under the stated core version. Give the result id and link to its run directory.

## Identity

| Level | What it means | Status |
|---|---|---|
| **self-asserted** | The lab's name, affiliation and contact are what the lab says they are. CI checks that the pull request's author is listed in the lab's file on `main` and that each report is signed by one of the lab's keys. The maintainer reads every registration but doesn't check identity documents. | Every lab today |
| **domain-verified** | The lab shows that it controls a domain, for example by publishing its key thumbprints on a page under that domain. | Planned; not yet offered |

The table shows each lab's level. Register one lab per organisation or group. Lab ids starting with `test-` are dry-run labs: CI accepts them, the table leaves them out, and the maintainer may remove them at any time. How to register and manage keys is in [labs/README.md](labs/README.md).

## What a submission contains

Each run is one directory, `results/<lab>/<test>-<UTC yyyymmddThhmmssZ>-s<seed>/`; the layout is in [results/README.md](results/README.md).

**Required in every run:**

| File | Contents |
|---|---|
| `report.json` | The signed test report (test-report 0.1), made by `be report` |
| `envelope.json` | The envelope exactly as used; its hash is in the report |
| `raw/samples.csv.gz` | The instrument's samples (`be-samples-csv/1`) |
| `raw/commands.csv.gz` | Every command sent, in order, with its result (`be-commands-csv/1`) |
| `raw/state.csv.gz` | The robot's reported state (`be-state-csv/1`) |
| `raw/accuracy.csv` | The instrument's accuracy checks before and after the run (`be-accuracy-csv/1`) |
| `raw/instrument/` | Instrument setup: calibration, placement, and the pre- and post-run checks |

**Optional:** `notes/NOTES.md`, and setup photos in `notes/photos/` that show no people.

Every file in a run must be listed with its hash in the report, so everything is added by `be report` before signing. The one exception is `WITHDRAWN.md`, which is added later (see below).

## Size limits

- No file over 20 MB, and no run over 50 MB in total.
- No Git LFS. CI refuses LFS pointer files and `.gitattributes` files under `results/`.
- Gzip the CSVs (`.csv.gz`). `raw/accuracy.csv` is small and may stay uncompressed.
- Never commit video. To refer to a video, give its URL and the sha256 of the file in `notes/NOTES.md`.

## Privacy

- Give the platform's make and model, or `withheld` for the model.
- No hostnames, usernames or home-directory paths. `be report` scrubs them before signing; check `NOTES.md` and the files in `raw/instrument/` as well.
- No location of any kind: no addresses, building or room names, GPS coordinates or IP addresses. Strip EXIF data, including GPS, from photos before `be report` signs them.
- Photos show the setup, never people. That includes faces, hands, bodies and reflections.
- The only personal data in a report is the signer's name and organisation, which the signer agrees to publish. The operator can be given as a role, such as "lab technician", rather than a name.
- If personal data is merged by mistake, tell the maintainer. The maintainer removes it, rewriting history if necessary, and marks the run withdrawn.

## Licence of submitted data

Submitted data is licensed under CC BY 4.0. Each lab grants that licence through the signed `license` and `rights_affirmed` fields of its report. Submit only data you have the right to license this way. The tooling in this repository is Apache-2.0.

## Permanence and withdrawal

- Merged results are permanent. Files in a run directory are never edited or deleted; CI refuses any change to a run that is already on `main` except adding `WITHDRAWN.md`.
- **To withdraw a result,** open a pull request that adds `results/<lab>/<run>/WITHDRAWN.md`. Say when you're withdrawing it, why, and which result replaces it, if any. The run stays in the repository and in the table, marked withdrawn. It no longer counts as a control or as a reproduction.
- **To correct a result,** submit a new run and withdraw the old one.
- Withdrawing a result doesn't revoke the CC BY 4.0 licence, which is irrevocable for copies already made.
- The maintainer may remove material that breaks the privacy rules or the law. The run is then marked withdrawn, with a note saying why.

## Roles and control runs

Every report has a `role`:

- **test**: a run of the test itself.
- **control**: a run designed to fail. The kit drives the arm with its control stream (one far target that the lab declares, approached in capped steps), so the tool leaves the envelope and the watchdog ends the run. A control that comes out FAIL shows that this setup (instrument, clock and scoring) can see an excursion when there is one.
- **rehearsal**: a run in simulation. Simulations always come out INCONCLUSIVE and never count as tests.

The table pairs each test run with the nearest control run by the same lab, for the same test, under the same envelope hash, that started within 12 hours of it. It then shows one of:

- **control failed**: the expected outcome;
- **control did not fail**: the control came out PASS or INCONCLUSIVE, so this setup hasn't shown that it can see an excursion. Read the test result with that in mind;
- **no control**: no control run was submitted.

Submit control runs in the same pull request as the test runs, or before them.

## Reproduction

- A reproduction is a new, separate run of the same test by a **different lab**, on its own setup. Its report names the original in `reproduces: <result id>`. The result id is `<lab>/<run directory>`, for example `acme-robotics/ev03-20261013T183000Z-s7`, and that result must already be on `main`.
- All of the maintainer's projects count as one lab, labelled "maintainer's own". Any lab whose lab file lists a maintainer's GitHub handle, or that `tools/maintainer.json` names, is one of them. A maintainer project re-running another maintainer project's result is a re-run, not a reproduction.
- Only test runs count, as reproductions or as originals. Controls and rehearsals never do.
- A reproduction should hold the same things constant: the make and model of machine, the gate's name and version, an envelope with the same zones, and the method version. The table checks only that the lab is different, that the test is the same, and that the original exists and isn't withdrawn. Readers can compare the rest in the two reports.
- The table shows every reproduction's verdict next to the original, whatever it is. A reproduction that comes out differently is shown in the same way.

## Claim language

This applies to `report.json`, `NOTES.md`, pull request titles and descriptions, and anything that cites results from this repository. CI checks `report.json` and `NOTES.md` against [`tools/claim-words.txt`](tools/claim-words.txt); review covers the rest.

- Don't use the words in that list: certified, certification, compliant, compliance, conformant, conformance, approved, endorsed, endorsement, accredited, guaranteed, safe, and the level names BE-1, BE-2 and BE-3.
- Don't say a robot or gate "passed" a test or is "verified", and don't state or imply a level.
- "Safety" on its own is fine: it names the topic. "Safe" as a description of a robot, gate or result is not.
- Say what was measured: "self-run", "consistent", "zero samples outside the envelope", "illustrative thresholds", "not reproduced", "reproduction by <lab>: FAIL".

For example, this is fine: "A self-run EV-03 with an external camera: zero samples outside the envelope; maintainer's own; illustrative thresholds; not yet reproduced; the raw data is public." These are not: "passed EV-03", "certified arm", "BE-2 robot".

## The maintainer's own results

Craig Merry maintains the method, the core package, the kit and this repository. He also maintains RCAN and the Robot Registry Foundation registry; the method does not depend on RCAN. His results go through the same pull requests and checks as everyone else's, and they are labelled "maintainer's own".

## Changes to this policy

Changes are made by pull request and reviewed by the maintainer, and the history is public. A change applies to submissions made after it is merged.
