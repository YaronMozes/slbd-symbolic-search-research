# Research Notes — Constraint-Aware BDD Variable Ordering for Symbolic Planning

**Status as of 2026-06-28.** Audience: project teammate getting up to speed.

This branch (`symbolic-landmark-guidance`) improves Fast Downward's **symbolic
bidirectional search** (`sbd`, BDD-based cost-optimal search, Torralba's
symbolic FD). The **main contribution is a positive, novel result**:
**constraint-aware BDD variable ordering**. The branch also contains a rigorous
**negative-result characterization** (seven falsified ideas) that motivated it.

---

## TL;DR — the contribution

**Constraint-aware variable ordering** (opt-in env var `SLBD_CONSTRAINT_ORDER`,
or option `sbd(constraint_order=true)`). GAMER orders BDD variables only for
**causal-graph** proximity, while the mutex/constraint BDDs conjoined into
*every* search step also depend on the order but are not optimized for. We add
influence edges between variables whose facts **co-occur in mutex / invariant
groups** (h² mutexes + exactly-one groups), then reuse GAMER's local-search
optimizer.

**Novelty (precise claim — verified by literature review).** *To our knowledge,
the first use in BDD-based symbolic classical planning of h² mutexes and
exactly-one invariant groups as co-occurrence edges in the GAMER-style static
variable-ordering objective.* The general idea — order variables sharing
constraints close together — is **known** in SAT/BDD and constraint compilation
(**FORCE** [Aloul et al.], **MINCE** [Aloul et al.], configuration-BDD ordering
[Narodytska & Walsh], OBDD knowledge compilation). So we frame this as a
**planning-specific adaptation** of constraint-graph ordering, NOT a new general
BDD-ordering paradigm. It differs from GamerPre (Kissmann & Hoffmann JAIR'14),
which adds *operator-precondition* co-occurrence edges, and from Torralba &
Alcázar / cGamer, which use mutexes/invariants for pruning/cBDDs/TRs but hold the
GAMER order fixed. Key related work to cite: FORCE, MINCE, Kissmann & Hoffmann
JAIR'14 (GamerPre), Torralba & Alcázar SoCS'13, Torralba AIJ'17. **Avoid** the
over-broad claim "constraint-aware BDD ordering" (pre-empted by FORCE/MINCE).

> **AUTHORITATIVE RESULT (551 instances, 18 domains, all-instances, 300s — see
> `slbd-results/co-coverage.csv`). This supersedes the smaller-sample numbers
> below, which were optimistic artifacts of first-8-instances/90s runs.**
> - **Coverage is essentially NEUTRAL.** At 90s/180s combined ties GAMER exactly
>   (297/307); at 300s combined is +1 (315 vs 314) but **NOT strict** — it loses
>   coverage on woodworking, gains on depot/sokoban. constraint-only is net −6
>   (worse). So **there is no clean coverage improvement at scale.**
> - **Cost-optimal preserved (0 cost mismatches).**
> - **What survives:** large *domain-specific speedups* (constraint-only −48%
>   pipesworld, −28% scanalyzer) and a faster per-instance *oracle*, realized by
>   the portfolio (never worse in wall-clock, 3× CPU). No single 1× ordering
>   beats GAMER; static selection-prediction fails.

Results (vs GAMER baseline, cost-optimal preserved everywhere):
- **Reliably shrinks the constraint (mutex) BDDs** wherever they exist: −33% to
  −94% in node count. The method does exactly what it's designed to.
- ~~Net coverage gain, no coverage regression~~ — **REFUTED at scale (see box
  above): coverage is ~neutral and `combined` is not strictly ≥ GAMER.** (The
  depot-p04 anecdote — GAMER 110s vs combined 67s — is real but domain-specific.)
- **Speed is an instance-level tradeoff** explained by a clear **mechanism**:
  CO wins when the constraint BDD is on the critical path (depot: −20% to −40%),
  loses when the search *frontier* dominates (blocks-14-0: +34%, even though the
  mutex BDD shrank 94%). This empirically answers an open question from the
  literature: *ordering for the search frontier alone is the wrong objective
  once constraint BDDs dominate.*

Key files: `src/search/symbolic/opt_order.{h,cc}` (the ordering),
`src/search/symbolic/sym_variables.cc` (the option), `original_state_space.cc`
(`MUTEX_BDD_SIZE:` diagnostic). Run baseline vs CO:
`sbd()` vs `sbd(constraint_order=true)` (weight via `co_weight`, default 1.0).
Reproduce: `compare-slbd.py --config-preset co-order ...`.

---

## Evaluation (systematic benchmark)

14 domains × 8 instances, `sbd()` vs `sbd(constraint_order=true)`, 90s timeout
(`slbd-results/co-order-big.txt`); key domains re-validated with 3 repeats
(`slbd-results/co-validate.txt`). All deterministic (RNG fixed seed 2011).

- **Coverage: 100/112 → 101/112** (+1, **no coverage regression** across 14
  domains); the gain is depot-p04 (CO solves at ~67s, GAMER needs ~110s, so CO
  wins at any limit in [67s,110s]). **Cost mismatches: none.**
- **Per-domain search-time ratio vs GAMER (<1 = CO faster), 3-repeat geomean:**
  - **tpp 0.89 (−11%)**, **pipesworld 0.89 (−11%)**, **zenotravel 0.96 (−4%)**,
    driverlog 0.97, gripper 0.98 — wins on constraint-heavy transport domains.
  - depot ~1.00 on common-solved (p03 −26% offset by p07 +55%) **but +1 coverage**.
  - **blocks 2.05 (+105%)** — regression (frontier-dominated, see Mechanism).
  - logistics/miconic have **zero** cross-variable mutex BDDs → CO is a no-op
    (small ratios there are timing noise; noise floor ≈ ±12% on sub-second tasks).
- **Mechanism** (`MUTEX_BDD_SIZE`): CO reliably shrinks the constraint BDD where
  it exists (−33% depot, −94% blocks). It *wins* when that BDD is on the critical
  path (transport domains), *loses* when the frontier dominates (blocks): the
  ordering good for the constraint BDD is bad for the frontier.
- **Negative finding (Option B, `SLBD_CO_SKIP_CAUSAL`):** skipping
  causal-neighbour co-occurrence edges keeps the wins but does **not** fix blocks
  — blocks skips only ~13% of edges, so its harmful edges are causally *distant*;
  the regression is a genuine constraint-vs-frontier conflict, not redundant
  edges. A per-instance selector (probe/portfolio) is left as future work.

**Honest headline:** novel, optimal-preserving, net coverage gain with no
coverage regression, ~11% speedups on constraint-heavy domains, with a
characterized regression on stacking domains.

### Three ordering signals (the main study)

We compare three orderings using the *same* GAMER optimizer, differing only in
edges — run via `compare-slbd.py --config-preset co-ablation`, or the parallel
`parallel-bench.py` (each task in its own temp dir + `--overall-memory-limit`;
needed because the binary is single-threaded and the harness is sequential):

1. **causal** = GAMER (causal-graph edges) — baseline.
2. **constraint-only** = mutex/invariant co-occurrence edges *only*, no causal
   (a FORCE/MINCE-style constraint-graph ordering; `constraint_only=true`).
3. **combined** = causal + constraint (our CO; `constraint_order=true`).

12 domains, 96 tasks, 90s, 3 GB/run; big effects re-validated at low contention
(`slbd-results/co-ablation-parallel.csv`, `co-ablation-clean.csv`). Cost
preserved throughout. Coverage /96: causal 90, constraint-only 88, **combined 91**.

- **No single ordering wins everywhere.** Per-domain geomean vs GAMER:
  constraint-only is **high-variance** — **pipesworld 0.52 (−48%)**,
  **scanalyzer 0.72 (−28%)**, but **satellite 3.58** and loses 2 coverage.
  combined is the **robust default** — +1 coverage, tpp 0.89, pipesworld 0.91,
  mostly neutral, only blocks slower (1.27).
- **The blocks regression is from *combining*, not the constraint signal** —
  on blocks-14-0, constraint-only (14.1s) beats GAMER (17.5s); combined (23.9s)
  is worst.
- **Per-domain ORACLE (best of the three) strictly dominates GAMER** — never
  slower, up to −48% (pipesworld), −28% (scanalyzer), −11% (tpp). Per-domain
  selection is feasible (vs the per-instance selector, which is future work).

**Reframed contribution:** not just "constraint-aware ordering" but a study of
how **causal (GAMER) and constraint (FORCE-style) ordering signals interact** in
symbolic planning — complementary, sometimes synergistic (depot/tpp), sometimes
antagonistic (blocks) — such that *selecting* among them beats the GAMER default.

### Realizing the oracle: a parallel ordering portfolio

`misc/tests/portfolio.py` runs the three orderings **concurrently** (each in its
own temp dir + memory limit) and returns the **first** solution → wall-clock =
the **per-instance** oracle over orderings. Symbolic search is single-threaded,
so this just uses otherwise-idle cores. It is **never slower than GAMER** (causal
is a component) and picks the right ordering per instance — verified:
constraint-only on pipes/scanalyzer/blocks, combined on depot/tpp, and **causal
on satellite (correctly avoiding constraint-only's 3.6× slowdown there)**.

- **Per-instance > per-domain:** on blocks-14-0 the portfolio picks
  constraint-only and beats GAMER (14.1s vs 17.5s *search*), even though the
  blocks *domain* average favours causal.
- **Honest metric:** report **search time** (clean). Portfolio total-wall-clock
  measurements are confounded by OS cache/driver-startup warming (GAMER run cold,
  portfolio warm), so the algorithmic claim rests on the search-time oracle, not
  raw wall-clock. Cost: 3× CPU for oracle wall-clock.
- The *intelligent* per-instance selector (predict the best ordering cheaply,
  without running all three) remains future work: a driver-level probe wastes
  work on fast-instance wins, and an in-engine probe-and-continue is a
  two-Cudd-manager refactor.

### Broadened evaluation + why prediction is hard (18 domains)

`slbd-results/co-ablation-broad.csv` (18 domains × 12, parallel-bench.py):
coverage causal **161**, constraint-only 160, **combined 164** (+3). On the 156
instances all three solve, **each ordering is the per-instance best ~⅓ of the
time** (causal 52, constraint-only 53, combined 51) and the **per-instance
oracle is 0.861 (−14% vs GAMER)** — selection is well justified.

We tried to build an **intelligent feature-based selector** (predict the fastest
ordering from setup-time features: co-occurrence-edge density, mutex-BDD size /
reduction). **It fails:** no rule beat GAMER ("co-edges/var<5→constraint-only"
gave 1.29; "mutex-reduction<0.3→constraint-only" gave 1.00). The relative speed
of the orderings is governed by **search dynamics, not static structure**
(scanalyzer wins at edges/var≈14 while depot loses at ≈15; blocks always loses,
sokoban usually wins). **Finding:** static selection-prediction is insufficient
here — one must observe behaviour, which is what the portfolio does implicitly.

**Why the portfolio is the right realization (not a fallback):** symbolic search
is single-threaded, so on a many-core machine the portfolio's ~3× CPU costs
**zero extra wall-clock** (idle cores). A CPU-frugal probe-based selector matters
only when cores are scarce; since static features can't predict the winner, it
needs an in-engine probe — well-scoped future work.

---

## Context — the negative-result characterization that led here

Before the ordering idea, seven approaches were measured and **all fail to beat
baseline `sbd`** (this is itself an original empirical study — the lecturer's
project-ideas doc lists "analysis of search-space properties" as a valid type):

1. Landmark direction guidance (coverage/agenda/meeting) — net-negative.
2. Meet-in-the-middle BDD signals (`meet_bdd`, `balance_bdd`) — inert/harmful.
3. Trend-based forward/backward budget control — split already near-optimal.
4. Forward-reachability pruning of the backward search — self-defeating.
5. Stronger generic pruning / heuristics — = SymBA*/abstractions = known work.
6. Beyond-h² acyclicity pruning (blocks) — sound+optimal but BDD-hostile.
7. (Plain) variable ordering GAMER-vs-FD — modest, domain-dependent.

**Root cause:** node-count direction selection is robust; the binding constraint
is *intrinsic BDD blow-up* (the BDD contents). That insight is exactly what
pointed us at shrinking the BDDs via **ordering** — and specifically the
constraint BDDs, which prior ordering work ignores.

---

## 0. Environment / how to build & run

- Build & run via **WSL/Ubuntu** (g++ 15, cmake 4). Windows paths map to
  `/mnt/c/...` in WSL.
- Build: `python3 build.py release64`
- Benchmarks live outside git at `../downward-benchmarks` (IPC suite).
- Results go to `slbd-results/` (git-ignored).
- Smoke: `./fast-downward.py <domain> <problem> --search "sbd()"`

---

## 1. The branch fix (do this first if rebuilding)

`src/search/search_engines/symbolic_landmark_search.cc` had **88 occurrences of
`retun`** (should be `return`) — a scripted-edit corruption. The source could
not compile, and `builds/release64/bin/downward` was built *before* the
corruption (binary 6 min older than the source). Fixed (`s/\bretun\b/return/g`,
verified the only change), rebuilt `release64`, and reproduced a known result
(gripper prob01 → cost 11) to re-anchor the baseline.

**Lesson for the team:** always confirm the binary is rebuilt from current
source before trusting any experiment.

---

## 2. Measurement infrastructure we added

- **`compare-slbd.py` presets** (`misc/tests/compare-slbd.py`):
  - `oracle` — fully UNMUZZLE landmark guidance (override every decision) and
    compare *follow* vs *anti* (polarity flip) for `weighted` and `agenda`
    scores. Isolates whether a direction signal carries any information.
  - `oracle-meet` — same protocol for the new `meet_bdd` / `balance_bdd` scores.
  - Run: `python3 misc/tests/compare-slbd.py --benchmark-dir <root>
    --config-preset oracle --include-domain gripper --include-domain depot ...`
- **Frontier trace** (env var `SLBD_TRACE_FRONTIER=1`): logs per decision the
  forward/backward reached-BDD `nodeCount`, next-step node estimates, and g
  values. Diagnostic only — does not change any decision. Profile a baseline
  with `slbd(lm_factory=lm_rhw(),lm_guidance=false,lm_lazy_landmarks=true)`.

---

## 3. The six idea-families (what, result, why)

### (1) Landmark direction guidance — net-negative
`slbd` uses landmarks to choose forward vs backward expansion, gated by a
node-slack "muzzle". Oracle result: **the more guidance overrides node-count,
the slower it gets** (≈37% overrides → 1.48× slower; near-0 overrides → ≈1.0×),
across both `tuning-agenda` and `domain-signal-balanced` runs. Unmuzzled
follow-vs-anti showed the "correct" polarity is *domain-dependent and
inconsistent* (depot wants one polarity, gripper the opposite), and the wrong
polarity destroys coverage (anti-weighted 20/32 solved vs sbd 30/32).
**Why:** landmark goal-progress is orthogonal to BDD representation size, which
is what actually drives symbolic-search cost.

### (2) Meet-in-the-middle BDD signals — inert / harmful
New landmark-free `lm_guidance_score` values (in the C++):
- `meet_bdd` = `numStates(frontier_d ∩ reached_opposite)` (connection proximity).
- `balance_bdd` = `numStates(reached_d)` (prefer the lagging perimeter).

`oracle-meet` result: `meet_bdd` is **structurally inert** — it fired on 0/558
decisions (the frontier∩opposite-reached overlap is ~empty until it *is* the
cut), so it just falls back to node-count; its apparent ≈5% "speedup" is noise
(follow and anti are behaviorally identical to sbd yet differ at ms-scale).
`balance_bdd` fires constantly but is harmful (follow 1.18× slower; anti loses
coverage 20/32, same blocks/depot blow-ups as the landmark anti configs).

### (3) Trend-based budget/split control — no headroom
We profiled forward vs backward reached-BDD sizes per layer
(`SLBD_TRACE_FRONTIER`) across 7 domains. Findings:
- Growth is smooth/predictable (stable per-layer ratios, e.g. depot bw ≈2.2×,
  fw ≈1.5×); asymmetry direction is domain-dependent.
- **But the two sides' peak BDD sizes end up balanced (1.0–1.7×)** because
  node-count already expands the smaller side — i.e. it already finds the
  near-optimal split. A trend-aware controller would make the same choice.
- Coverage failures (depot p04, logistics-10, miconic-20) are **intrinsic BDD
  blow-up**: at the required meeting depth *both* sides are huge (100K–millions
  of nodes). Re-balancing a ≤1.7× asymmetry can't escape an exponential.

### (4) Forward-reachability pruning of backward — self-defeating
Idea: conjoin a forward-reachability over-approximation R⁺ into the backward
frontier to remove spurious (forward-unreachable) states. Killed by `sfw` vs
`sbw` vs `sbd` measurement: on blocks-12-0 *neither* unidirectional search
finishes but `sbd` does — i.e. computing R⁺ *is* the intractable forward search.
On depot-p03 forward-only takes 22.7s while `sbd` takes 2.4s. **Bidirectional
search already *is* the pruning** (the meeting avoids materializing R⁺).

### (5) Stronger generic pruning / heuristics — known work
The planner already does **h² mutexes + dead-ends, cross-applied to both
directions** (forward-detected invariants prune backward; see
`src/search/mutex_group.h` and `src/search/symbolic/original_state_space.cc`).
Default `MUTEX_EDELETION`, size 100K. The known way past blind search is
abstraction heuristics (SymBA*, Gamer PDBs) — reproducing it doesn't meet the
"novel" bar. `addDeadEndStates()` (dynamic dead-end injection) exists but is
only used by the heuristic `sym_astar` path, not blind `sbd`/`slbd`.

### (6) Beyond-h² acyclicity pruning (blocks) — sound but BDD-hostile
Implemented (env var `SLBD_ACYCLIC`, length cap `SLBD_ACYCLIC_LEN`): parse
`on(X,Y)` SAS⁺ atoms, build a "state contains a short on-cycle" BDD, inject as
dead-ends via `addDeadEndStates` for both directions. h² only captures
2-cycles, so ≥3-cycles are an h²-missed, sound, optimality-preserving invariant.
**Result on blocks-12-0:** plan cost 34 preserved (sound ✓), but the constraint
is BDD-hostile: 132 *two*-cycles → a **4.35M-node** dead-end BDD and **4×
slower** search; 3-cycles blew up to 2.8 GB (OOM). 2-cycles are already in h²
(prune nothing new) yet cost 4× — pure constraint-application overhead. The set
of cyclic states is too large/irregular for a compact BDD. Net loss.

---

## 4. Bottom line

Blind symbolic bidirectional UCS + h² mutex pruning is **mature** and close to
its frontier. node-count direction selection is robust; the binding constraint
is intrinsic BDD blow-up (the contents), which direction/schedule/cheap-pruning
heuristics cannot escape. Every "obvious" improvement is either already done,
self-defeating, or hits a representational wall.

---

## 5. Code changes on this branch (all additive / behavior-preserving by default)

- `symbolic_landmark_search.cc`:
  - Fixed the 88 `retun`→`return` typos.
  - New `lm_guidance_score` values `meet_bdd`, `balance_bdd` (landmark-free).
  - `SLBD_TRACE_FRONTIER` env-var per-decision frontier diagnostic.
  - `SLBD_ACYCLIC` / `SLBD_ACYCLIC_LEN` acyclicity dead-end pruning prototype.
- `misc/tests/compare-slbd.py`: `oracle` and `oracle-meet` presets.

Default `slbd`/`sbd` behavior is unchanged; new scores and env vars are opt-in.

---

## 6. Recommended next steps (pick based on the project's novelty bar)

1. **Package the characterization** as the deliverable — original empirical
   study (oracle methodology + the six falsified families). Negative results
   with a clear mechanism are legitimate; this one is thorough.
2. **Variable ordering** — the one untouched lever that directly shrinks BDDs
   (the proven bottleneck). Inherently domain-specific; best odds for a positive
   domain-specific result, though the area is researched (reproduce-risk).
3. **Reframe the problem** — drop the mature optimal baseline; a satisficing
   setting (e.g. landmark-based symbolic decomposition) has much more room but
   gives up optimality.

> The acyclicity idea *could* be revisited with a non-BDD encoding
> (transition-relation restriction or auxiliary topological-rank variables),
> but the 2-cycle evidence (huge bad-set, net overhead) suggests low payoff.
