# From Landmark Guidance to Constraint-Aware Variable Ordering in Symbolic Optimal Planning

**Course project — AI and Autonomous Systems (09608).** Draft report.
Authors: Yaron Mozes, Galit K.

> Status: working draft built from the experiments in this repo
> (`RESEARCH_NOTES.md`, `slbd-results/`). All numbers are from a laptop-scale,
> non-IPC protocol — stated explicitly in §8. Prose to be polished; citations to
> be formatted.

---

## Abstract

Symbolic (BDD-based) search is a leading approach to cost-optimal classical
planning; its performance is dominated by **BDD size**. This project has two
parts. **First**, we investigated our original hypothesis — that **landmarks can
guide the direction choice** in symbolic *bidirectional* search — and, with a
purpose-built "oracle" test protocol, found a clean **negative result**: landmark
direction guidance does not help, because the baseline node-count rule is already
near-optimal and landmark goal-progress is *orthogonal* to BDD size. We then
systematically eliminated several related direction/schedule/pruning ideas, all
for the same root cause: **the binding constraint is intrinsic BDD size (the BDD
*contents*), not which side you expand.** **Second**, that insight led us to
**variable ordering** — the main lever on BDD size. We observe that symbolic
planners conjoin *constraint BDDs* (h² mutexes, exactly-one invariant groups)
into search at every step, and that their size depends on the order, yet the
GAMER ordering objective ignores them. Inspired by constraint-graph ordering in
SAT (FORCE/MINCE) and portfolio selection (SATzilla), we add **mutex/invariant
co-occurrence edges** to the ordering objective. Doing so exposed a further
finding: **the deployed GAMER optimizer ignores its own edge weights** (an
existence-only objective); our initial "combined" ordering was therefore an
unweighted topology blend, and it did not beat GAMER (coverage neutral,
constraint-only worse on average yet spectacular on specific domains, e.g. −42%
on pipesworld). After fixing the objective to be weight-aware — provably
baseline-preserving — the **weight-fixed combined ordering is coverage-≥-GAMER
in every one of the 18 domains (+3 net at 300 s) with overall speed 0.991×, at
1× CPU**, and a **seed-variance control** shows this is attributable to the
constraint signal, not optimizer randomness (a fresh seed is 1.04× and loses
coverage in two domains). The orderings remain **strongly complementary**: a
per-instance oracle over signals and seeds reaches **~0.73×**, which we realize
with a parallel **portfolio** (3× CPU on otherwise-idle cores); **cheap static
selection of the ordering fails.** All orderings preserve cost-optimality.

---

## 1. Introduction

Cost-optimal classical planning asks for a provably cheapest plan. **Symbolic
search** represents large sets of states as Binary Decision Diagrams (BDDs) and
explores them with set operations (Edelkamp & Kissmann's GAMER; Torralba's
symbolic Fast Downward). The dominant cost factor is **BDD size**.

This project began with a different idea than where it ended. We set out to
improve **symbolic *bidirectional* search** — which expands a forward frontier
(from the initial state) and a backward frontier (from the goal) and stops when
they meet — by using **landmarks** to make a smarter choice of *which frontier to
expand next*. After building this and testing it rigorously, we found it does not
work, and we found *why*. The reason — that the real bottleneck is BDD size, not
the search schedule — pointed us at **variable ordering**, where our positive (if
incremental) contribution lies.

We present both parts, because the negative result is itself a contribution (it
is, to our knowledge, the first study of landmark-guided direction selection for
symbolic search) and because it motivates the second part.

---

## 2. Background

- **Symbolic bidirectional uniform-cost search (`sbd`).** Blind, cost-optimal,
  BDD-based forward + backward search. At each step it must choose a **direction**
  (which frontier to expand); the baseline rule picks the side whose next BDD
  image is estimated smaller (**node-count**). Baseline planner: Torralba's
  symbolic Fast Downward.
- **Landmarks.** Facts (or actions) that must hold (or occur) at some point on
  *every* plan. Central to modern heuristic search (LAMA, LM-cut), but rarely used
  inside BDD-based symbolic search.
- **GAMER variable ordering.** Build an "influence graph" over SAS⁺ variables
  with edges from causal-graph dependencies; minimize a linear-arrangement
  objective by randomized local search so dependent variables sit close.
- **Constraint BDDs.** h² mutexes + exactly-one invariant groups, conjoined into
  the frontiers to prune spurious states (Torralba & Alcázar, *Constrained
  Symbolic Search*, SoCS 2013).

---

## 3. Part I — Landmark-guided direction selection, and why it failed

### 3.1 The hypothesis and what we built

In bidirectional symbolic search the per-step choice of *direction* (expand
forward or backward) affects how the two frontiers grow and meet. The baseline
picks the cheaper-looking side by node-count. We hypothesized that **landmarks**
— which signal progress toward the goal — could choose a *better* direction.

We implemented a new search engine, `slbd`
(`src/search/search_engines/symbolic_landmark_search.cc`, ~1,300 lines), that:
- computes landmarks with Fast Downward's landmark factories and indexes each as
  a BDD;
- at each direction decision, scores the two frontiers by their landmark status,
  with a **family of scoring functions** (coverage, weighted coverage, progress,
  ordered, meeting, agenda, agenda-weighted, agenda-meeting), a **polarity**
  (prefer the more- or less-covered side), a **scope** (score over states *seen*
  vs the active *frontier*), and several gates;
- crucially, a **node-slack gate**: landmarks may override the node-count choice
  *only* when the two sides' BDD-size estimates are within a slack — otherwise it
  defers to node-count. Landmarks therefore only affect direction, **never** plan
  cost: optimality is preserved.

### 3.2 How we tested it — the "oracle" protocol

A guidance method that rarely fires tells you little. To isolate whether the
landmark signal carries *any* useful information, we built a deliberately
aggressive test (presets `oracle`, `oracle-meet` in `misc/tests/compare-slbd.py`):

> **Fully unmuzzle** the guidance (remove the slack gate; override *every*
> decision) and compare **follow** (take the landmark-preferred direction) vs
> **anti** (take its exact opposite, via a polarity flip), against the baseline.

The logic: if *follow* is faster than *anti*, the signal is informative; if they
are equal, it is noise; if *anti* is faster, the signal is *inverted* (and we
should just flip it). This cleanly separates "the signal is wrong" from "the
gating is wrong."

### 3.3 The result: it doesn't help, and we know why

Measured across two independent benchmark runs (`slbd-results/`):

- **The more the guidance overrides node-count, the slower search gets.** Plain
  `slbd` (~37% of decisions overridden) was **1.48× slower** than the baseline;
  configurations that overrode almost nothing were ≈ baseline. Forcing overrides
  only made it worse.
- **The "correct" polarity was domain-dependent and inconsistent** — depot
  preferred one direction, gripper the opposite — and the *wrong* polarity
  **destroyed coverage** by driving BDD blow-ups.
- **Mechanism (the key finding).** Node-count direction selection is already
  *robust and near-optimal*. Landmarks measure **semantic goal-progress**, which
  is **orthogonal to BDD representation size** — and BDD size is what actually
  determines symbolic-search cost. So the landmark-preferred direction frequently
  steers into the *larger-BDD* side. The node-slack gate, in hindsight, was simply
  a **muzzle** limiting the damage of a net-negative signal.

To our knowledge no prior work had tried landmarks for direction selection in
symbolic bidirectional search, so **this negative result is itself novel**:
*landmark-guided direction selection does not improve blind symbolic bidirectional
search, because the binding cost is BDD size, not goal-distance.*

### 3.4 The same wall, four more times

We then eliminated the natural follow-on ideas, each measured to ground rather
than argued:

- **Meet-in-the-middle BDD signals** (prefer the direction whose frontier most
  overlaps the opposite side's reached set; `meet_bdd`, `balance_bdd`) — **inert
  or harmful**: the overlap is essentially empty until the search is already at
  the meeting point, so the signal cannot act early enough.
- **Trend-based budget control** (allocate forward/backward expansion effort by
  the observed BDD-growth trend) — **no headroom**: profiling the per-layer
  frontier sizes showed node-count already finds a near-optimal split (the two
  sides' peak BDDs end up balanced, 1.0–1.7×), so a trend-aware controller would
  make the same choice.
- **Forward-reachability pruning of the backward search** — **self-defeating**:
  removing forward-unreachable (spurious) backward states needs the
  forward-reachable set, but computing that *is* the intractable forward search —
  which is the very reason one uses bidirectional search.
- **Beyond-h² acyclicity pruning** (blocks/depot: the `on`-relation must be
  acyclic, an invariant h² mutexes miss) — **sound and optimal but BDD-hostile**:
  encoding "no cycle" produces a multi-million-node constraint BDD that slows
  search ~4× and provides no net benefit; the search blow-up is dominated by
  *valid* configurations, not cycles.

### 3.5 The pivot

The consistent root cause across all of Part I: **the per-step direction /
schedule / cheap-pruning decisions are robust — node-count is near-optimal — and
the binding constraint is the *size of the BDDs themselves* (their contents), not
which side you expand.** To actually improve the planner you must make the BDDs
*smaller*. The dominant lever on BDD size is the **variable ordering** — which is
Part II.

---

## 4. Part II — Constraint-aware variable ordering

### 4.1 Related work and the precise novelty claim

The general idea — *order variables that share constraints close together* — is
**well established outside planning**: **FORCE** and **MINCE** (Aloul et al.)
build a hypergraph from CNF clauses and place co-constrained variables close to
shrink BDDs; configuration-BDD compilation (Narodytska & Walsh) and knowledge
compilation do likewise; and **SATzilla** establishes that complementary
orderings/solvers win on non-overlapping instances and that **portfolios** are
the standard robustness answer.

Within *symbolic planning*, prior orderings use the **causal graph** (GAMER),
**precondition** co-occurrence (GamerPre; Kissmann & Hoffmann, JAIR 2014), or
action syntax — but **not mutex/invariant co-occurrence**. Mutexes/invariants are
used for pruning, constraint BDDs, transition relations, and SAS⁺ variable
selection; Torralba & Alcázar even note the mutex-BDD size depends on the order
but do not optimize for it.

**Precise, defensible claim (verified by a literature review):** *to our
knowledge, this is the first use in BDD-based symbolic classical planning of h²
mutexes and exactly-one invariant groups as co-occurrence edges in the
GAMER-style variable-ordering objective.* We frame it explicitly as a
**planning-specific adaptation** of constraint-graph ordering (FORCE/MINCE) +
portfolio selection (SATzilla) — *not* a new general BDD-ordering paradigm, and we
avoid the over-broad claim "constraint-aware BDD ordering."

### 4.2 Method

We modify GAMER's influence graph (`src/search/symbolic/opt_order.cc`): after
adding the causal-graph edges, we add an (accumulating) edge between any two SAS⁺
variables whose facts co-occur in the same mutex or exactly-one invariant group,
then run GAMER's existing optimizer unchanged. This yields three orderings, all
using the *same* optimizer:

1. **causal** — GAMER (causal edges only). *Baseline.*
2. **constraint-only** — mutex/invariant co-occurrence edges only, no causal (a
   FORCE/MINCE-style ordering). Option `constraint_only=true`.
3. **combined** — causal + constraint edges. Option `constraint_order=true`.

Exposed as `sbd(constraint_order=…, constraint_only=…)`; ≈ 40 lines of core code.
Because the ordering changes only the BDD representation and never which plan is
found, **cost-optimality is preserved by construction** (confirmed: 0 cost
mismatches across all runs).

We also implement a **parallel ordering portfolio** (`misc/tests/portfolio.py`):
run the three orderings concurrently, return the first solution. Its wall-clock
equals the per-instance oracle; it is never slower than GAMER (causal is a
component) and uses the otherwise-idle cores a single-threaded symbolic search
leaves.

### 4.3 The "oracle" (virtual best) — what it is and is not

Throughout, the **oracle** denotes the *per-instance minimum* over the three
orderings (the "virtual best" of the algorithm-selection literature). It is **not
a deployable algorithm** — it has perfect hindsight, picking each instance's
winner after the fact. Because GAMER is one of its three choices, the oracle is
**≤ GAMER on every instance** by construction; it beats every single ordering
*only because the winner changes from instance to instance* (complementarity). It
measures the *size of the opportunity*; the **portfolio** realizes it at 3× CPU,
and a 1× **selector** would realize it if one could predict the winner (see §9).

---

## 5. Experimental setup

- **Planner:** `sbd` (symbolic bidirectional UCS), build `release64`.
- **Domains:** 18 IPC optimal-STRIPS domains spanning constraint-heavy and
  frontier-heavy structure (depot, blocks, gripper, miconic, logistics00,
  driverlog, zenotravel, satellite, rovers, tpp, pipesworld-notankage, and the
  IPC-2008 opt set: elevators, scanalyzer, pegsol, sokoban, transport,
  woodworking; plus freecell).
- **Harness.** We wrote a **parallel runner** (`misc/tests/parallel-bench.py`):
  each (instance, ordering) job runs in its own temp directory with a per-run
  memory limit, across a thread pool — necessary because the planner is
  single-threaded and the stock harness is sequential (it left us using ~1 of 16
  cores). For landmark Part I we used `compare-slbd.py` with the oracle presets.
- **Metrics.** Coverage (solved within limit), plan cost (optimality check),
  search time, constraint-BDD size, and a few setup-time features.

---

## 6. Honest protocol limitation (read this)

We evaluate on **real IPC domains** but with a **reduced, laptop-scale
protocol**, *not* the IPC competition protocol:

| | This work | IPC standard |
|---|---|---|
| Domains | 18 (subset of `optimal_strips`) | ~45 (full suite) |
| Instances/domain | all, capped ~40 | all |
| Timeout | 90–300 s | 1800 s |
| Memory | 3–4 GB | ~8 GB |
| Hardware | one laptop (i7-13620H, 16 threads, 32 GB) | compute cluster |

The full IPC protocol (~1,800 instances × 1,800 s) is cluster-scale and
infeasible here. **Our coverage numbers are therefore a proof-of-concept on
standard domains, not directly comparable to published IPC coverage.** To avoid a
cherry-picked timeout we record solve-times and report coverage at multiple
cutoffs.

---

## 7. Results (Part II)

### 7.0 A defect in the ordering optimizer, and the weight-fixed ordering

Reviewing the optimizer, we found that GAMER's linear-arrangement objective
(`compute_function` and both incremental swap-delta evaluations) used the
influence value **only as an existence test — edge weights were never read.**
Consequences: our earlier `co_weight` sweep was a no-op (its "flatness" was the
bug, not saturation), and "combined" was an *unweighted topology* blend in which
large mutex groups (k(k−1)/2 pairs each) swamped the sparse causal edges —
explaining why combined was *worse than constraint-only* on blocks.

We made the objective weight-aware (verified: causal-only weights are all 1, so
the **GAMER baseline is bit-identical**; a `binarize` control reproduces the old
behaviour exactly). The weight-fixed combined ordering (**comb-w**) was then
validated at the authoritative protocol (551 instances, 18 domains, all
instances capped 40, 300 s; `slbd-results/co-definitive.csv`), with a
**seed-variance control** (`causal-s42`: plain GAMER under a different RNG seed)
to separate signal from local-search randomness:

| | causal (GAMER) | causal-s42 (seed control) | **comb-w** |
|---|---|---|---|
| Coverage @90/180/300 s | 290/305/310 | 293/308/312 | **294/307/313** |
| Domains with coverage loss | — | **2** (rovers, woodworking) | **0 (never worse)** |
| Overall speed geomean | 1.000 | 1.040 | **0.991** |
| Cost mismatches | 0 | 0 | 0 |

- **comb-w is coverage-≥-GAMER in every domain** (+3 net: depot, logistics,
  sokoban) and ≥ at every cutoff — the strict-coverage property the unweighted
  combined ordering *failed* (it lost woodworking). Run-to-run coverage variance
  is ≈ ±4 instances, so we emphasise the never-worse-per-domain pattern (also
  reproduced in an independent smaller run) over the +3 total.
- **The seed control shows this is signal, not randomness:** a fresh seed is
  *slower* overall (1.040) and *loses* coverage in two domains, while comb-w is
  0.991 and never worse. Seed variance alone does not reproduce the effect.
- comb-w is faster in 12 of 18 domains (scanalyzer 0.80, depot 0.83,
  woodworking 0.84, pipesworld 0.88), slower in 6 (sokoban 1.28, freecell 1.21);
  hard blocks instances still individually regress.
- A companion 7-configuration experiment (`co-weightfix.csv`) decomposes the
  portfolio opportunity: a 3-seed oracle reaches 0.871, the signal orderings
  0.780, and **seeds + signals together 0.728** — the constraint signal adds
  diversity beyond randomness (e.g. pipesworld: all causal seeds ≈ 1.0,
  constraint-only 0.53).

### 7.1 Coverage under the *unweighted* orderings was neutral (cost-optimal preserved)

18 domains, all instances (cap 40), 300 s, 3 GB (`slbd-results/co-coverage.csv`;
551 instances). Coverage at search-time cutoffs:

| cutoff | causal (GAMER) | constraint-only | combined |
|---|---|---|---|
| 90 s | 297 | 292 | 297 |
| 180 s | 307 | 302 | 307 |
| 300 s | 314 | 308 | 315 |

`combined` ties GAMER at 90/180 s and is only **+1 at 300 s**, and **not strictly
≥ GAMER** (it loses one woodworking instance, gains on depot/sokoban).
`constraint-only` is net **−6**. **0 cost mismatches.** → **No coverage
improvement at scale.** (An earlier, smaller sample suggested "+3, no regression";
that was a first-8-instances/90 s artifact, corrected here.)

### 7.2 Speed: strong complementarity, no single-ordering winner

Clean low-contention run (4 workers, 120 s; `slbd-results/co-clean-final.csv`; 184
commonly-solved instances). Geomean search time vs GAMER (<1 = faster):

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
  constraint-only worse (1.19) — yet constraint-only is *spectacular* on a few
  domains (pipesworld, scanalyzer, transport) and *catastrophic* on others
  (rovers/satellite ≈ 3.5×).
- **Each ordering is the per-instance best on ≈ ⅓ of instances** — the
  complementarity SATzilla describes, here *within* symbolic-planning orderings.
- **The per-instance oracle is −16% and ≤ 1.0 in every domain.**

### 7.3 Realizing the oracle: the portfolio

The portfolio reaches the oracle wall-clock — never slower than GAMER, much
faster on the complementary domains (it picks constraint-only on pipesworld,
combined on tpp, and *causal* on satellite, correctly avoiding the 3.5×
ordering). Cost: **3× CPU**, essentially free here since symbolic search is
single-threaded and machines leave many cores idle.

### 7.4 Mechanism

The constraint signal reliably shrinks the constraint BDDs (−33% to −94% in node
count). It *wins* when those constraint BDDs are on the critical path, and *loses*
when the search frontier dominates and the constraint-informed order hurts the
frontier (e.g. blocks). The effect is domain-structural, not universal.

---

## 8. Selecting the ordering at ~1× CPU: task features fail, build features work

If selection captures the oracle, can we pick the right ordering *without*
running all candidates (avoiding k× CPU)? This took three attempts:

**(a) Task-level features fail.** Simple setup-time predictors
(co-occurrence-edge density, mutex-BDD size/reduction) never beat GAMER
(best rule ≥ 1.0): the relative speed of the orderings is governed by search
dynamics, not task structure (scanalyzer wins at the same edge density where
depot loses).

**(b) Build-time features succeed.** The feature nobody had measured: the
**size of the transition relations actually built under each candidate
ordering** (~1 s to construct, no search; TRs participate in every image
operation). Offline, `argmin TR-size over {causal, comb-w, constraint-only}`
scores **0.894 vs always-GAMER** — the first selection rule to beat the
baseline. It is a weak ranker (≈60 % pairwise concordance) but reliably avoids
the *catastrophic* orderings. (We also closed the optimizer-convergence
question: 10× optimization budget changes neither the objective value reached
nor search time — and over-optimizing the proxy can even grow the real TRs,
confirming the arrangement objective is only loosely coupled to BDD size.)

**(c) Economics need a presolve schedule.** The naive selector (probe all three,
then search) makes the *right* choices (+2 coverage in its bench) but pays a
flat ~2–3 s (probes + repeated translate) that yields **1.49× wall** on the
trivial instances that dominate benchmarks by count. The deployable version
(`misc/tests/tr_select.py`) therefore runs **plain GAMER for 5 s first**
(solving most instances with zero overhead) and only probes+switches on hard
instances. Final honest bench (sequential, all overhead counted):
**wall-clock 0.996 vs GAMER overall — parity — and 0.924 (−8 %) on hard
instances (> 10 s)**, with **+1 coverage** (depot-p04); best domains
woodworking 0.87, scanalyzer 0.89, depot 0.92; worst transport 1.20.

**Takeaway:** per-instance ordering selection at ~1× CPU is achievable — but
only from *build-time* evidence (what the BDDs actually look like), not from
task statistics, and only with amortization-aware scheduling. The k×-CPU
portfolio remains the stronger (and simpler) option when idle cores exist;
a learned SATzilla-style model on build features is the natural next step.

---

## 9. Conclusion and future work

**Part I (negative, original):** landmark-guided direction selection — and a
family of related direction/schedule/pruning ideas — do **not** improve blind
symbolic bidirectional search, because node-count selection is robust and the
binding cost is intrinsic BDD size, not the search schedule. We established this
with a clean "oracle" (follow-vs-anti) test protocol.

**Part II (positive but incremental):** bringing constraint-graph ordering
(FORCE/MINCE) and portfolio selection (SATzilla) into symbolic planning's
mutex/invariant structure — a first in this line — yields **strongly
complementary** orderings (per-instance oracle −16%, never worse per domain),
realized by a parallel portfolio at 3× CPU. **No single 1× ordering beats GAMER**,
coverage is neutral, cost-optimality preserved, and **static selection-prediction
fails.**

Honestly: this is an **incremental, mixed** outcome — a rigorous negative result
plus a complementarity study and a portfolio — not a new dominant algorithm.

**Future work:** (1) a *learned* (SATzilla-style) or *probe-based* in-engine
selector to realize the oracle at ~1× CPU; (2) typed/weighted mutex edges and
additional ordering signals to enrich the portfolio; (3) the full IPC protocol on
a cluster; (4) constraint-aware ordering for heuristic symbolic search (SymBA*).

---

## Reproducibility

All code, options, presets, and result CSVs are in this repo. Entry points:
`sbd(constraint_order=…, constraint_only=…)`; the `slbd(...)` engine and `oracle`
presets (Part I); `misc/tests/parallel-bench.py`; `misc/tests/portfolio.py`;
`misc/tests/feature_analysis.py`; `slbd-results/co-*.csv`. RNG is fixed (seed
2011) — orderings are deterministic.

## Key references

FORCE, MINCE (Aloul et al.); Edelkamp & Kissmann, GAMER; Kissmann & Hoffmann,
*BDD Ordering Heuristics for Classical Planning* (JAIR 2014); Torralba & Alcázar,
*Constrained Symbolic Search* (SoCS 2013); Torralba et al. (AIJ 2017); Xu et al.,
*SATzilla* (JAIR 2008); Narodytska & Walsh (IJCAI 2007); Richter & Westphal, LAMA
(landmarks).
