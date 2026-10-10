<!--
For result submissions, withdrawals and lab registrations. Maintainer changes
(tools/, .github/, kits.lock, POLICY.md) don't use this template.
A green check means "consistent", not "passed". See README.md and POLICY.md.
-->

**This pull request is:**
- [ ] new result run(s)
- [ ] a withdrawal (`WITHDRAWN.md` added to a run already on `main`)
- [ ] a lab registration or key change

**Lab id:** `<lab>`
**Result id(s):** `<lab>/<test>-<UTC yyyymmddThhmmssZ>-s<seed>`

## Every pull request
- [ ] Every changed path is inside `results/<my lab>/` or, for a lab change, inside `labs/<my lab>/`. Nothing else.
- [ ] I am listed in `github_handles` of `labs/<my lab>/lab.json` on `main` (for a new lab: in the file I am adding).

## New result runs
- [ ] Each report is signed with a key listed for my lab on `main`, and not revoked.
- [ ] `be verify <run_dir> --keys keyring.json` succeeds locally, using the core version named in the report, which is listed in `kits.lock`.
- [ ] I copied the runs with `be submit`, which checks the sizes and refuses unreleased core versions and simulations not marked `rehearsal`.
- [ ] Each run has `report.json`, `envelope.json`, `raw/samples.csv.gz`, `raw/commands.csv.gz`, `raw/state.csv.gz`, `raw/accuracy.csv` and `raw/instrument/`. Photos and `notes/NOTES.md` are optional.
- [ ] No file over 20 MB, no run over 50 MB, CSVs gzipped, no Git LFS. Video is linked in `NOTES.md` as a URL and its sha256, not committed.
- [ ] Privacy: the machine model is given or `withheld`; hostnames, usernames and paths are scrubbed; there is no location anywhere; photos show no people and have no EXIF/GPS data.
- [ ] Claim language: `report.json` and `NOTES.md` use none of the words in `tools/claim-words.txt`, and nothing says the robot "passed".
- [ ] Role is `test`, `control` or `rehearsal`. Control runs for these tests are in this pull request or already on `main`.
- [ ] If a run reproduces another result: `reproduces` names its result id, and my lab is a different lab from the one that ran it.
- [ ] The signed report grants CC BY 4.0 (`license`, `rights_affirmed`), and I have the right to grant it.
- [ ] I understand these results are self-run and are not evidence for any certification, registry tier or regulatory claim, and must not be cited as such.

## Withdrawal
- [ ] `WITHDRAWN.md` gives the date, the reason, and the result id that replaces this one, if any. The run's other files are unchanged.
