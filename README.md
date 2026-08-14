# Constraint-Aware Variable Ordering in Symbolic Optimal Planning

Research code and data for the final project in *Artificial Intelligence and
Autonomous Systems* (00960208) at the Technion, Faculty of Data and Decision
Sciences, by **Yaron Mozes** and **Galit Kadzelshvily**.

**Paper: [`paper/main.pdf`](paper/main.pdf)** (sources: [`paper/main.tex`](paper/main.tex))

## What this is

Symbolic (BDD-based) search is a leading approach to cost-optimal classical
planning, and its cost is dominated by BDD size — which depends critically on the
*variable ordering*. The GAMER-family ordering used by state-of-the-art planners
optimizes causal-graph proximity only. Torralba and Alcázar (SoCS 2013) observed
that the mutex/invariant **constraint BDDs** — compiled, under SymK's default
e-deletion scheme, into the transition relations that every image operation
applies — also depend on that ordering, and suggested exploring orders derived
from the constraints.

This repository implements and evaluates exactly that idea — mutex and
exactly-one invariant **co-occurrence edges** added to the ordering objective —
inside **SymK**, on the complete IPC optimal-STRIPS suite (66 domains, 1,847
instances) at 1800 s / 8 GB / 1 CPU per task.

## Results in one paragraph

A **pre-registered**, double-replicated A/B study shows that a *per-domain policy*
(the ordering enabled on five signal domains, stock SymK elsewhere) improves stock
SymK by **+6 coverage — exactly +6 in both replicates** — and accelerates
*woodworking* by **1.55×**, with zero plan-cost mismatches. Enabled *universally*,
however, the ordering is a **tightly bounded null** (coverage 1145 vs 1139; wall
ratio geomean 1.005, 95 % CI [0.97, 1.05]). Instrumentation explains why: the
ordering reliably shrinks **transition relations** (geomean 0.867, p ≈ 3·10⁻²¹) but
TR size is a poor proxy for runtime (ρ = 0.202, R² ≈ 0.04), while **peak
search-BDD size** — which the ordering leaves unchanged — is the operative
quantity (ρ = 0.593). We also report empirical ceiling bounds on selection and
restart wrappers, and two methodological findings: regression-to-the-mean in
"hard instance" subsets, and cross-year duplicate instances in the IPC suite
(322 of the 1,847 evaluated instances) that break domain-wise cross-validation.

**Scope, stated plainly:** the confirmed improvement covers five of 66 IPC domains;
suite-wide the method is a measured zero. Both are reported.

## Repository map

### The method
| Path | Contents |
|---|---|
| [`symk-patch/`](symk-patch/) | **The final method**, as deployed in SymK: constraint co-occurrence edges + the weight-aware objective repair (`opt_order.cc`, `opt_order.h`) |
| [`src/search/symbolic/opt_order.{cc,h}`](src/search/symbolic/) | The same method in our research fork, with all ablation switches |
| [`src/search/symbolic/sym_variables.cc`](src/search/symbolic/) | Option plumbing (`constraint_order`, `co_weight`, `constraint_only`) |
| [`src/search/symbolic/original_state_space.cc`](src/search/symbolic/) | Diagnostics: `MUTEX_BDD_SIZE`, `TR_SIZE`, probe mode |

### The experiments
| Path | Contents |
|---|---|
| [`PREREG-ab-policy.md`](PREREG-ab-policy.md) | **Pre-registration** — design and gates, committed *before* the data existed, with outcomes appended |
| [`misc/tests/ab_policy.py`](misc/tests/ab_policy.py) | The per-domain policy (`SIGNAL_DOMAINS`) + the confirmatory A/B driver |
| [`misc/tests/symk_sel_bench.py`](misc/tests/symk_sel_bench.py) | Full-suite SymK driver (includes the repaired domain-file pairing) |
| [`misc/tests/parallel-bench.py`](misc/tests/parallel-bench.py) | Parallel benchmark runner (crash-safe, resumable) |
| [`misc/tests/tr_select.py`](misc/tests/tr_select.py), [`tr_select_symk.py`](misc/tests/tr_select_symk.py) | The TR-probe selector studied in the ceiling-bounds section |
| [`data/`](data/) | **The result CSVs behind every claim**, with a column guide and merge instructions |
| [`RESEARCH_NOTES.md`](RESEARCH_NOTES.md) | Full lab notebook, including the ideas that failed |

The paper in [`paper/`](paper/) is the report of record; `RESEARCH_NOTES.md` is
the working notebook behind it.

## Reproducing

```bash
python3 build.py release64          # add -DUSE_LP=NO if COIN-OR linking fails
```

Our fork:

```bash
./fast-downward.py DOMAIN.pddl PROBLEM.pddl --search "sbd(constraint_order=true, co_weight=2.0)"
```

SymK with the patch (env-var controlled): apply `symk-patch/` over stock SymK, then

```bash
SLBD_CONSTRAINT_ORDER=1 SLBD_CO_WEIGHT=2.0 \
  ./fast-downward.py DOMAIN.pddl PROBLEM.pddl --search "sym-bd()"
```

The confirmed policy uses the base weight directly — group-size normalization
(`SLBD_CO_NORM`) and causal-edge skipping (`SLBD_CO_SKIP_CAUSAL`) are optional
ablations and are **disabled** in it. Benchmarks come from
[aibasel/downward-benchmarks](https://github.com/aibasel/downward-benchmarks).

## Attribution and license

This is a fork of **[Torralba's symbolic Fast Downward](https://gitlab.com/atorralba/fast-downward-symbolic)**,
itself derived from **[Fast Downward](http://www.fast-downward.org/)** — see
[`README-fast-downward.md`](README-fast-downward.md) for the upstream copyright
notice and the **GNU GPL v3** terms, which apply to this repository. The files in
[`symk-patch/`](symk-patch/) are modifications of **[SymK](https://github.com/speckdavid/symk)**
(David Speck et al., also GPL v3) and are redistributed under the same license.
Our own contributions are released under GPL v3 as well.

Please cite the original authors for the planners; this repository only adds the
constraint-aware ordering, its evaluation, and the analysis reported in the paper.
