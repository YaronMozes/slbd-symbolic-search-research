# Analysis scripts

Regenerate the paper's numbers from the CSVs in [`../../data/`](../../data/).
Paths are resolved relative to this directory, so a plain checkout runs as-is
(`python3 <script>.py`; numpy and matplotlib are needed only for `mechfig.py`).

| Script | Produces |
|---|---|
| `check_gates.py` | Tables 1–3: the pre-registered gates, per-domain A/B breakdown, and the suite-wide comparison with domain-clustered bootstrap CIs |
| `deepcheck.py` | The Results/Methodology figures (freecell, floortile, noise pairs, regression-to-the-mean; the duplicate audit additionally needs `BENCHMARKS=/path/to/downward-benchmarks`) |
| `audit_check.py` | Selection-null checks (tie-exhausted) and the two-ordering oracle |
| `cvcheck.py` | Leave-one-domain-out vs leave-one-family-out ridge CV |
| `mechfig.py` | `paper/mechanism.pdf` (Figure 2); Spearman with tie-aware average ranks |
| `common.py` | Shared helpers (tie-aware ranks, conventional medians, exact sign test, the fix7 merge) |

## Not reproducible from here

Two figures in the paper are **estimates whose generator was not retained**, and
are labelled as such in the text: the k-ordering oracle projections (+20…+33 for
k = 3…8) and the 150-point selector parameter sweep. No conclusion rests on
them alone.
