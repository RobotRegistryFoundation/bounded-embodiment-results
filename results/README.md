# Results

```
results/<lab>/<test>-<UTC yyyymmddThhmmssZ>-s<seed>/
```

| Part | Meaning | Example |
|---|---|---|
| `<lab>` | The lab id, registered in `labs/<lab>/lab.json` on `main` | `acme-robotics` |
| `<test>` | The report's `test`, in lower case without punctuation | `EV-03` becomes `ev03` |
| `<UTC>` | The report's `run.started_at`, in UTC | `20261013T183000Z` |
| `<seed>` | The report's `generator.seed` | `s7` |

The **result id** is `<lab>/<run directory>`, for example `acme-robotics/ev03-20261013T183000Z-s7`. A reproduction names it in its `reproduces` field.

## Run directory

`be report` builds this layout and `be submit` copies it here. The layout is fixed by the core's interface contract (CONTRACT.md in the method repository).

```
<run>/report.json             signed test report 0.1
<run>/envelope.json           the envelope exactly as used (its hash is in the report)
<run>/raw/samples.csv.gz      be-samples-csv/1:  t_s,point,x_m,y_m,z_m,valid,residual_m
<run>/raw/commands.csv.gz     be-commands-csv/1: seq,t_sent_s,t_done_s,strategy,payload_json,result,error_kind
<run>/raw/state.csv.gz        be-state-csv/1:    t_s,source,joint,value
<run>/raw/accuracy.csv        be-accuracy-csv/1: phase,label,ref_x_m,ref_y_m,ref_z_m,meas_x_m,meas_y_m,meas_z_m,n
<run>/raw/instrument/...      calibration and setup files (listed in report.raw_data)
<run>/notes/NOTES.md          optional; added by `be report` before signing
<run>/notes/photos/*.jpg      optional; setup only, no people
<run>/WITHDRAWN.md            only if the lab later withdraws the result; added by pull request, not signed
```

- Coordinates are in metres in the envelope's `frame`. Every `t_s` is seconds on the run's single timebase.
- Every file except `WITHDRAWN.md` is listed with its sha256 and size in `report.raw_data`, and `be verify` refuses unlisted files.

## Rules CI enforces here

- A pull request adds runs for **one lab**, adds `WITHDRAWN.md` to runs of that lab already on `main`, or both. It changes nothing outside `results/<lab>/`.
- Once a run is on `main`, its files never change. The only addition allowed is `WITHDRAWN.md`.
- File and directory names use letters, digits, `.`, `_` and `-`, and don't start with `.`. No symbolic links, submodules or Git LFS pointers.
- No file over 20 MB, and no run over 50 MB in total.
- Each new run passes `be verify` under the core version its report names, which must be listed in `kits.lock`, using the lab's keys from `main`.
- `report.json` and `NOTES.md` contain none of the words in `tools/claim-words.txt`.

Everything under `results/` is licensed CC BY 4.0 by the lab that submitted it. See [POLICY.md](../POLICY.md) for the full rules.
