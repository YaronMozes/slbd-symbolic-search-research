# Analysis scripts

The scripts that produce the numbers in the paper, added after an external audit
noted that no committed code regenerated them.

All read the CSVs in [`../../data/`](../../data/). Paths are set at the top of
each file; edit them if your checkout lives elsewhere. Run with `python3`.

| Script | Produces |
|---|---|
| `check_gates.py` | The pre-registered A/B gates and per-domain breakdown (Tables 1–2) |
| `deepcheck.py` | Re-derives the Results and Methodology figures from raw data |
| `recompute.py` | The corrected regression-to-the-mean, noise, restart and selector figures |
| `cvcheck.py` | Leave-one-domain-out vs leave-one-family-out ridge CV ($R^2 \approx 0.06 / 0.05$) |
| `mechfig.py` | `paper/mechanism.pdf` (Figure 2) |
| `audit_check.py` | Selection-null checks and the two-ordering oracle |

## Not included

Two figures in the paper are **not** reproducible from this directory, and are
labelled as estimates in the text:

- the $k$-ordering oracle projections ($+20 \dots +33$ for $k = 3 \dots 8$),
- the 150-point selector parameter sweep.

Both came from a simulator calibrated on the measured two-ordering oracle that
was not retained. They are indicative only; no conclusion in the paper rests on
them alone.
