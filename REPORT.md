# Constraint-Aware Variable Ordering for Symbolic Optimal Planning

**Course project — AI and Autonomous Systems (09608).** Draft report.
Authors: Yaron Mozes, Galit K.

> Status: working draft built from the experiments in this repo
> (`RESEARCH_NOTES.md`, `slbd-results/`). Numbers are from a laptop-scale,
> non-IPC protocol — stated explicitly in §6. Prose to be polished.

---

## Abstract

Symbolic (BDD-based) search is a leading approach to cost-optimal classical
planning, and its performance is dominated by **BDD size**, which in turn is
governed by the **variable ordering**. The state-of-the-art GAMER ordering
chooses this order from the **causal graph**. We observe that these planners
*also* conjoin **constraint BDDs** (h² mutexes and exactly-one invariant groups)
into the search at every step, and that the size of those constraint BDDs also
depends on the order — yet the ordering objective ignores them. Inspired by
constraint-graph variable ordering from SAT/BDD model checking (FORCE, MINCE)
and by per-instance algorithm selection (SATzilla), we add **mutex/invariant
co-occurrence edges** to GAMER's ordering objective, producing two new orderings
(*constraint-only* and *combined*). On 18 IPC optimal-STRIPS domains we find that
the three orderings are **strongly complementary** — each is the per-instance
best on roughly one third of instances — and that a **per-instance oracle is
~16% faster than GAMER while never slower in any domain**. However, **no single
ordering beats GAMER on its own** (combined is ~neutral, constraint-only is worse
on average but spectacular on specific domains, e.g. −42% on pipesworld), and
**coverage is essentially neutral**. We realize the oracle with a simple parallel
**portfolio** (exploiting the idle cores a single-threaded symbolic search
leaves), and show that **static per-instance selection of the ordering fails**.
All orderings preserve cost-optimality.

---

## 1. Introduction

Cost-optimal classical planning asks for a provably cheapest plan. **Symbolic
search** tackles it by representing large sets of states as Binary Decision
Diagrams (BDDs) and exploring them with set operations rather than state-by-state
(Edelkamp & Kissmann's GAMER; Torralba's symbolic Fast Downward). The dominant
cost factor is the **size of the BDDs**, which can vary by orders of magnitude
with the **variable ordering** — the linear order of the state variables inside
the BDD.

GAMER computes this order from the **causal graph**: variables that influence one
another through actions are placed close together. Separately, these planners
exploit **state constraints** — binary (h²) mutexes and exactly-one invariant
groups — by conjoining *constraint BDDs* into the search frontiers to prune
unreachable/spurious states (Torralba & Alcázar). Those constraint BDDs are
applied at *every* step, and their size also depends on the variable order — but
the ordering objective is computed *only* from the causal graph and never
optimizes for the constraints it later conjoins.

**This paper asks:** is the *constraint structure* a useful variable-ordering
signal in symbolic planning, and if so, when does it help? We answer empirically.

---

## 2. Background

- **Symbolic bidirectional uniform-cost search (`sbd`).** Blind, cost-optimal,
  BDD-based forward+backward search. Baseline planner: Torralba's symbolic Fast
  Downward.
- **GAMER variable ordering.** Build an "influence graph" over SAS⁺ variables
  with edges from causal-graph dependencies; minimize a linear-arrangement
  objective by randomized local search so dependent variables sit close.
- **Constraint BDDs.** h² mutexes + exactly-one invariant groups, conjoined into
  the frontiers to remove spurious states (Torralba & Alcázar, *Constrained
  Symbolic Search*, SoCS 2013).

---

## 3. Related work and the precise novelty claim

The general idea — *order variables that share constraints close together* — is
**well established outside planning**:

- **FORCE** (Aloul et al.) and **MINCE** (Aloul et al.) build a hypergraph from
  CNF clauses/constraints and place co-constrained variables close, explicitly to
  shrink BDDs.
- Configuration-BDD compilation (Narodytska & Walsh) and OBDD knowledge
  compilation use weighted constraint graphs for the same purpose.
- **SATzilla** and the SAT algorithm-selection literature establish that
  different orderings/solvers are best on *non-overlapping* instance sets, and
  that **portfolios** are the standard robustness answer.

Within *symbolic planning*, prior orderings use the **causal graph** (GAMER),
**precondition** co-occurrence (GamerPre; Kissmann & Hoffmann, JAIR 2014), or
action syntax — but **not mutex/invariant co-occurrence**. Mutexes/invariants are
used elsewhere (pruning, cBDDs, transition relations, SAS⁺ variable selection),
and Torralba & Alcázar explicitly note the mutex-BDD size depends on the order
but do not optimize for it.

**Precise, defensible claim (verified by literature review):** *to our
knowledge, this is the first use in BDD-based symbolic classical planning of h²
mutexes and exactly-one invariant groups as co-occurrence edges in the
GAMER-style variable-ordering objective.* We explicitly frame our work as a
**planning-specific adaptation** of constraint-graph ordering (FORCE/MINCE) +
portfolio selection (SATzilla) — **not** a new general BDD-ordering paradigm. We
avoid the over-broad claim "constraint-aware BDD ordering."

---

## 4. Method

We modify GAMER's influence graph (file `src/search/symbolic/opt_order.cc`):
after adding the causal-graph edges, we add an edge (accumulating weight) between
any two SAS⁺ variables whose facts co-occur in the same mutex or exactly-one
invariant group, then run GAMER's existing optimizer unchanged. This gives three
orderings, all using the *same* optimizer:

1. **causal** — GAMER (causal edges only). *Baseline.*
2. **constraint-only** — mutex/invariant co-occurrence edges only, no causal
   (a FORCE/MINCE-style ordering). Option `constraint_only=true`.
3. **combined** — causal + constraint edges. Option `constraint_order=true`.

Exposed as `sbd(constraint_order=…, constraint_only=…)`; ~40 lines of code. The
orderings change only the BDD representation, **never** which plan is found, so
**cost-optimality is preserved by construction** (confirmed empirically: 0 cost
mismatches across all runs).

We also implement a **parallel ordering portfolio** (`misc/tests/portfolio.py`):
run the three orderings concurrently and return the first solution. Its
wall-clock equals the per-instance oracle; it is never slower than GAMER (causal
is a component) and uses the otherwise-idle cores a single-threaded symbolic
search leaves.

---

## 5. Experimental setup

- **Planner:** `sbd` (symbolic bidirectional UCS), build `release64`.
- **Domains:** 18 IPC optimal-STRIPS domains spanning constraint-heavy and
  frontier-heavy structure (depot, blocks, gripper, miconic, logistics00,
  driverlog, zenotravel, satellite, rovers, tpp, pipesworld-notankage, and the
  IPC-2008 opt set: elevators, scanalyzer, pegsol, sokoban, transport,
  woodworking; plus freecell).
- **Harness:** custom parallel runner (`misc/tests/parallel-bench.py`), each run
  in its own temp dir with a per-run memory limit, across a thread pool.
- **Metrics:** coverage (solved within limit), plan cost (optimality check),
  search time, and constraint-BDD size.

---

## 6. Honest protocol limitation (read this)

We evaluate on **real IPC domains** but with a **reduced, laptop-scale
protocol**, *not* the IPC competition protocol. We state this plainly:

| | This work | IPC standard |
|---|---|---|
| Domains | 18 (subset of `optimal_strips`) | ~45 (full suite) |
| Instances/domain | all, capped ~40 | all |
| Timeout | 90–300 s | 1800 s |
| Memory | 3–4 GB | ~8 GB |
| Hardware | one laptop (i7-13620H, 16 threads) | compute cluster |

The full IPC protocol (~1,800 instances × 1,800 s) is cluster-scale and
infeasible here. Consequently **our coverage numbers are a proof-of-concept on
standard domains and are not directly comparable to published IPC coverage.** To
avoid a cherry-picked timeout, we record solve-times and report **coverage at
multiple cutoffs**.

---

## 7. Results

### 7.1 Coverage is neutral (cost-optimal preserved)

Rigorous run: 18 domains, all instances (cap 40), 300 s, 3 GB
(`slbd-results/co-coverage.csv`; 551 instances). Coverage at search-time cutoffs:

| cutoff | causal (GAMER) | constraint-only | combined |
|---|---|---|---|
| 90 s | 297 | 292 | 297 |
| 180 s | 307 | 302 | 307 |
| 300 s | 314 | 308 | 315 |

- **`combined` ties GAMER** at 90/180 s and is only **+1 at 300 s**, and **not
  strictly ≥ GAMER** (it loses one instance on woodworking, gains on
  depot/sokoban). **`constraint-only` is net −6.**
- **0 cost mismatches** — optimality preserved everywhere.

**Conclusion: there is no coverage improvement at scale.**

### 7.2 Speed: strong complementarity, no single-ordering winner

Clean low-contention run (4 workers, 120 s; `slbd-results/co-clean-final.csv`;
184 commonly-solved instances). Geomean search time vs GAMER (<1 = faster):

| | constraint-only | combined | **oracle (best-of-3)** |
|---|---|---|---|
| **overall** | **1.19** | **1.02** | **0.84 (−16%)** |
| pipesworld | 0.58 | 0.86 | 0.53 |
| scanalyzer | 0.70 | 0.97 | 0.70 |
| transport | 0.84 | 0.96 | 0.77 |
| tpp | 0.92 | 0.83 | 0.75 |
| gripper | 0.96 | 0.90 | 0.85 |
| satellite | 3.46 | 0.94 | 0.87 |
| rovers | 3.93 | 0.99 | 0.89 |
| blocks | 1.13 | 1.43 | 0.94 |
| woodworking | 1.02 | 1.22 | 0.88 |

- **No single 1× ordering beats GAMER overall:** combined ≈ neutral (1.02),
  constraint-only worse (1.19) — but constraint-only is *spectacular* on a few
  domains (pipesworld, scanalyzer, transport) and *catastrophic* on others
  (rovers/satellite ≈ 3.5×).
- **Each ordering is the per-instance best on ≈ ⅓ of instances** (causal 52 /
  constraint-only 53 / combined 51 on an earlier 156-instance sample) — the
  classic complementarity SATzilla describes.
- **The per-instance oracle is −16% and ≤ 1.0 in *every* domain** — selection is
  a genuine, strict (per-domain) improvement.

### 7.3 Realizing the oracle: the portfolio

The parallel portfolio reaches the oracle wall-clock — never slower than GAMER,
much faster on the complementary domains (e.g. it picks constraint-only on
pipesworld, combined on tpp, and *causal* on satellite, correctly avoiding the
3.5× ordering). Its cost is **3× CPU**, which is essentially free here: symbolic
search is single-threaded and modern machines leave many cores idle.

### 7.4 Mechanism

The constraint signal reliably shrinks the constraint BDDs (−33 % to −94 % in
node count). It *wins* when those constraint BDDs are on the critical path, and
*loses* when the search frontier dominates and the constraint-informed order
hurts the frontier (e.g. blocks). The win is therefore domain-structural, not
universal.

---

## 8. Why a cheap selector fails (negative result)

If selection captures −16 %, can we pick the right ordering *without* running all
three (avoiding the 3× CPU)? We tested simple setup-time predictors
(co-occurrence-edge density, mutex-BDD size/reduction). **They fail** — no rule
beat GAMER (`co-edges/var<5 → constraint-only` gave 1.29; `mutex-reduction<0.3`
gave 1.00). The relative speed of the orderings is governed by **search
dynamics, not static structure** (scanalyzer wins at high edge density while
depot loses at the same density). This mirrors SATzilla's finding that effective
selection needs *learned* empirical models, not hand rules — left as future work.

---

## 9. Conclusion and future work

We brought **constraint-graph variable ordering** (FORCE/MINCE) and **portfolio
selection** (SATzilla) into symbolic *planning's* mutex/invariant structure — to
our knowledge a first in this line — and characterized the result honestly:

- **Coverage neutral; cost-optimal preserved.**
- **No single 1× ordering beats GAMER**, but the orderings are **strongly
  complementary** (per-instance oracle −16 %, never worse per domain).
- A **parallel portfolio** realizes the oracle at 3× CPU (free on idle cores).
- **Static selection-prediction fails**; the conflict is search-dynamic.

This is an **incremental, honestly-mixed** result, not a new dominant algorithm.

**Future work:** (1) a *learned* (SATzilla-style) or *probe-based* in-engine
selector to realize the oracle at ~1× CPU; (2) typed/weighted mutex edges and
additional ordering signals to enrich the portfolio; (3) the full IPC protocol on
a cluster; (4) constraint-aware ordering for heuristic symbolic search (SymBA*).

---

## Reproducibility

All code, options, presets, and result CSVs are in this repo. Key entry points:
`sbd(constraint_order=…, constraint_only=…)`;
`misc/tests/parallel-bench.py`; `misc/tests/portfolio.py`;
`misc/tests/feature_analysis.py`; `slbd-results/co-*.csv`. RNG is fixed
(seed 2011) — orderings are deterministic.

## Key references

FORCE (Aloul et al.); MINCE (Aloul et al.); Edelkamp & Kissmann, GAMER;
Kissmann & Hoffmann, *BDD Ordering Heuristics for Classical Planning* (JAIR 2014);
Torralba & Alcázar, *Constrained Symbolic Search* (SoCS 2013); Torralba et al.
(AIJ 2017); Xu et al., *SATzilla* (JAIR 2008); Narodytska & Walsh (IJCAI 2007).
