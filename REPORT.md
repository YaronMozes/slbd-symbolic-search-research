# Constraint-Aware Variable Ordering in Symbolic Optimal Planning: A Confirmed Per-Domain Improvement to SymK, and a Bounded Null with Its Mechanism

**Course project — final report draft.** Authors: Yaron Mozes, Galit K.
All numbers below are re-derived from the result CSVs in this repository and were
independently verified; the headline experiment was **pre-registered**
(`PREREG-ab-policy.md`, committed before the data existed).

---

## Abstract

Symbolic (BDD-based) search is a leading approach to cost-optimal classical
planning, and its cost is governed by BDD size, which in turn depends on the
variable ordering. The GAMER-family ordering used by state-of-the-art planners
optimizes causal-graph proximity only; Torralba and Alcázar (SoCS 2013) noted
that the mutex/invariant constraint BDDs conjoined into every search step also
depend on the ordering, and left optimizing for them as future work. We
implement and evaluate exactly that idea — mutex/invariant co-occurrence edges
added to the ordering objective — inside **SymK (IPC-2023)**, on the complete
IPC optimal-STRIPS suite (66 domains, 1,847 instances, 1800 s / 8 GB / 1 CPU).
Four results. **(1) A confirmed per-domain improvement:** under a pre-registered,
replicated, interleaved A/B, a per-domain policy (the ordering enabled on five
signal domains) beats stock SymK by **+6 coverage (exactly +6 in both
replicates)** and accelerates woodworking by **1.55×**, with optimality
preserved. **(2) A tightly bounded suite-wide null:** enabled everywhere, the
ordering changes nothing overall (coverage 1145 vs 1139; time ratio geomean
1.0000, 95 % CI [0.96, 1.05]) — the interval excludes even a 5 % effect.
**(3) The mechanism:** the objective does what it was designed to do — it
shrinks transition relations (geomean 0.867, p ≈ 3·10⁻²¹) — but TR size turns
out to be a poor proxy for search cost (ρ = 0.20 with runtime, R² ≈ 0.04),
whereas peak search-BDD size is the operative quantity (ρ = 0.59); the ordering
leaves peak size unchanged (geomean 1.026, p = 0.12). Shrinking the TR is not
shrinking the search. **(4) Empirical ceiling bounds:** restart schedules,
static learned selection, and probe-based selection over these orderings cannot
produce a detectable suite-wide win on this protocol, because the achievable
oracle gain sits below the measured ±17-instance replicate noise floor. We also
report methodological findings of independent interest: a worked demonstration
that "faster on hard instances" claims can be pure regression to the mean, and
the discovery that cross-year duplicate instances in the IPC suite silently
invalidate leave-one-domain-out cross-validation.

---

## 1. Introduction

Cost-optimal classical planning asks for provably cheapest plans. Symbolic
search explores sets of states represented as binary decision diagrams (BDDs);
its runtime and memory are dominated by BDD sizes, and BDD sizes depend —
sometimes exponentially — on the variable ordering. The orderings used in
practice (GAMER and descendants, inherited unchanged by SymK, the IPC-2023
winner) optimize a causal-graph proximity objective.

These planners also exploit state invariants: h² mutexes and exactly-one groups
are compiled into constraint BDDs that participate in every search step. The
constraint BDDs' sizes depend on the same variable ordering, but the ordering
objective ignores them. Torralba and Alcázar (SoCS 2013) observed this and
explicitly proposed optimizing the ordering for the mutex BDDs as future work.
To our knowledge no one had implemented or evaluated it. We did, through three
stages: an initial study in a 2016-era research planner, the discovery and
repair of a defect in the deployed ordering optimizer, and a full-scale,
pre-registered evaluation inside SymK.

The project began elsewhere: with the hypothesis that landmarks could guide
direction selection in symbolic bidirectional search. That hypothesis is false,
and we report it briefly (§3) because its post-mortem — the finding that BDD
size, not search scheduling, is the binding constraint — is what motivated the
ordering work.

## 2. Background

**Symbolic bidirectional uniform-cost search.** Blind, cost-optimal, BDD-based
forward+backward search (`sbd` in Torralba's symbolic Fast Downward; `sym-bd`
in SymK). Each step expands the frontier whose estimated image is cheaper.

**GAMER ordering.** An influence graph over SAS⁺ variables is built from
causal-graph edges; a randomized local search minimizes a weighted
linear-arrangement objective (sum of w·d² over edges). SymK inherits this
scheme unchanged (verified against SymK's source).

**Constraint BDDs.** h² mutexes and exactly-one invariant groups, conjoined
into frontiers (`MUTEX_EDELETION` in both planners) to prune spurious states.

## 3. Part I: landmarks do not help direction selection (negative)

We built a landmark-guided direction selector for `sbd` (~1,300 lines: landmark
sets as BDDs; coverage/agenda/meeting scores; polarity and gating). A dedicated
"oracle" protocol — force the guidance to override every decision, and compare
*follow* against its exact inverse (*anti*) — isolates whether the signal
carries information. Findings: the more the guidance overrides the baseline
node-count rule, the slower search gets (1.48× at ~37 % override rate); the
better polarity is domain-inconsistent; and the mechanism is that landmark
goal-progress is orthogonal to BDD size, which is what actually determines
symbolic search cost. Four follow-on ideas (meet-in-the-middle signals, trend
budget control, forward-reachability pruning, beyond-h² acyclicity constraints)
fail for the same root cause; the acyclicity constraint is sound but
BDD-hostile (multi-million-node constraint, ~4× slowdown). Conclusion: the
bottleneck is the size of the BDDs themselves — which pointed at ordering.

## 4. The constraint-aware ordering

**Idea.** After GAMER's causal edges, add an influence edge (accumulating
weight w) between every pair of variables whose facts co-occur in a mutex or
exactly-one group; run the unmodified optimizer. Options/env expose the weight
and ablations. The ordering never changes which plan is found, so optimality is
preserved by construction — and empirically: **zero cost mismatches across
~10,000 compared runs** in two independent codebases.

**A defect found in the deployed optimizer.** GAMER's objective (in this
codebase lineage, including SymK) evaluated edge *existence* but never read
edge *weights* — in both the full evaluation and the incremental swap deltas.
Weighted variants were silently unweighted, and our combined ordering was an
unweighted topology blend dominated by large groups (k(k−1)/2 pairs each). We
fixed the objective to be weight-aware; with causal-only edges all weights are
1, so the baseline is provably (and verifiedly) bit-identical. Two further
latent defects found during audit (an unsound partial exactly-one invariant on
the abstraction path; a dead-end list routed to the wrong direction) were fixed
and are off every result path in this report.

**Novelty.** The specific signal — mutex/invariant co-occurrence in the
GAMER-family arrangement objective — implements Torralba & Alcázar's stated
future work and is, per our literature review, previously unevaluated.
Constraint-graph ordering in general is classical (FORCE, MINCE in SAT/BDD);
GamerPre uses precondition co-occurrence; we claim only the planning-specific
instantiation and its evaluation.

## 5. Result 1 (headline): a confirmed per-domain improvement of SymK

Two independent full-suite runs showed replicated per-domain effects (gains on
parking-opt14 +3/+3, barman-opt11 +1/+2, tpp +1/+1; a Bonferroni-surviving
woodworking speedup at weight 2 with a monotone dose–response: w=0.5 → 0.87,
w=1 → 0.84, **w=2 → 0.586**, faster on 38/47, significant under every standard
paired test (exact Wilcoxon p = 4·10⁻⁷, sign test p = 2.5·10⁻⁵) and
comfortably Bonferroni-surviving; and replicated *losses* on freecell
and tetris). This motivates a **per-domain policy**: enable the ordering (with
per-domain weight) on the five signal domains; stock SymK elsewhere.

**Pre-registered confirmatory A/B** (design and gates committed before the
data): stock vs policy on all instances of the five signal domains, two
replicates, stock/policy adjacent in the job queue (contention-symmetric),
1800 s / 8 GB / 1 CPU per run, 480 runs.

| pre-registered gate | requirement | outcome |
|---|---|---|
| Primary: coverage contrast | ≥ +4 | **+6 (rep 1) and +6 (rep 2)** — CONFIRMED |
| Secondary: woodworking speed | wall geomean ≤ 0.85 | **0.647** (n = 94 pairs; faster on 70/94) — CONFIRMED |
| Guard: no replicated domain loss | none | **zero losses in any replicate** — PASS |
| Integrity: cost mismatches | zero | **0 / 480 runs** — PASS |

Per-domain (policy − stock, rep1/rep2): parking-opt14 +2/+3, barman-opt11
+1/+2, tpp +1/+1, woodworking-opt08 +1/+0, woodworking-opt11 +1/+0.

**Claim:** the per-domain policy improves stock SymK by +6 coverage on the
signal domains — exactly replicated — and accelerates woodworking ~1.55×,
optimality preserved. Scope stated plainly: five domains of 66; the policy
equals stock elsewhere by construction.

## 6. Result 2: the suite-wide bounded null for the always-on ordering

Full suite, corrected data (after repairing a harness defect, §9): coverage
**stock 1145, always-on 1139, TR-probe selector 1150** of 1,847. The always-on
delta (−6) is well inside the measured ±17 replicate floor; on the 1,617
instances untouched by the harness defect the discordant pairs are 12 gains vs
14 losses (exact sign test p = 0.85). Runtime on commonly solved instances:
**geomean 1.00 (0.998), median exactly 1.000**, domain-clustered 95 % CI
**[0.96, 1.05]** — a bounded zero, not an absence of evidence. A seed control shows the
perturbation is real but unbiased: sd(log time-ratio) = 0.44 against a
0.20–0.23 same-config noise floor. The largest reproducible single effects are
*against* the always-on ordering (freecell 25→20, replicated; floortile ~1.56×
slower on 20/20, Bonferroni-corrected p = 0.005) — which is precisely why the
correct instrument is the per-domain policy of §5.

## 7. Result 3: the mechanism

From the instrumented planner (both orderings, per-instance BDD statistics):

- The ordering **achieves its design goal**: transition-relation size shrinks
  (geomean 0.867, median 0.954, smaller on 58.7 % of the 1,361 active
  instances, p ≈ 3·10⁻²¹; recorded at preprocessing, selection-bias-free).
- It **does not move what matters**: peak search-BDD nodes geomean 1.026,
  median 1.000, p = 0.12 (n = 759).
- **TR size is a bad proxy; peak size is the operative quantity**:
  ρ(log TR-ratio, log time-ratio) = 0.202 (R² ≈ 0.04) vs
  ρ(log peak-ratio, log time-ratio) = **0.593** (p ≈ 2·10⁻⁶⁹), computed on
  the 718 active instances whose TR changed (0.199 / 0.580 on all 759 — same
  conclusion).
- The suite-wide sign is explained by **asymmetry**: the ordering grows the TR
  on 36.7 % of active instances (median growth 1.20×), and growth costs more
  (time ratio 1.211) than shrinkage saves (0.946).
- **Scope**: the method is a literal no-op on 25.6 % of instances (no
  cross-variable mutex groups; 13 of 66 domains).

This single decoupling — TR vs peak — predicts the null, the per-domain
character, and the selector results below, rather than rationalizing them
after the fact.

## 8. Result 4: ceiling bounds — why no variant of "always-on plus selection" can win here

- **Measured noise floor**: sd(log runtime) = 0.196 per run (290 same-batch
  replicate pairs); 29 % of identical-config repeats differ by >20 %;
  identical-config coverage drifts by 28 instances between full runs → a
  ±17-instance resolution limit for coverage claims on this protocol.
- **Two-ordering oracle**: +12 coverage — below the floor. Modeled k-ordering
  oracles (calibrated to reproduce the measured +12): ≈ +20/+24/+29/+33 for
  k = 3/4/6/8; even a perfect k = 6 selector sits at ~1–1.7× the floor, and
  real selectors capture ~a third of oracle value.
- **Restart schedules** (single 1800 s budget): zero stock-unsolved instances
  are rescued by the other ordering within 600 s (3 within 900 s), while 48
  solved instances need >900 s; every tested budget split loses coverage;
  break-even dispersion would need σ = 1.23 vs measured ≤ 0.9.
- **Static learned selection**: an apparent leave-one-domain-out R² = 0.30
  collapsed to ≈ −0.05 under leave-one-*family*-out — the IPC suite contains
  299 cross-year duplicate instances (108 bit-identical) that leak across
  domain splits. Consistent with Kissmann & Hoffmann's verdict that ordering
  quality is unpredictable before the BDDs are built.
- **TR-probe selection**: the TR signal is real but weak — when the selector
  deviates from stock it is right 70.8 % of the time (p = 5.4·10⁻⁴, replicated
  at 71.2 %) — yet no configuration in a 150-point parameter sweep is
  net-positive in wall-clock, and short time limits are actively harmed by the
  probe tax.

## 9. Methodological findings

1. **Regression to the mean, demonstrated**: selecting "hard" instances by the
   *baseline's* runtime makes our ordering look 20 % faster (0.796); the mirror
   selection makes it look 6 % slower (1.064); the symmetric estimator is 0.908
   with a 54.5 % win rate. The pattern reproduces in a second codebase
   (0.741 / 1.406 / 1.025). Per-method hard-instance speedups computed the
   usual way are not evidence.
2. **IPC duplicate leakage**: cross-year duplicate instances silently break
   domain-wise cross-validation; deduplication (123 duplicate sets) is required
   before any learned-model claim on this suite.
3. **Harness forensics**: a domain-file pairing bug (per-instance domain files
   matched to the wrong domain) produced 225 instant failures *and* — the
   diagnostic tell — three cost values below the known optima, because a wrong
   pairing that parses yields a valid solution to the wrong task. The defect
   was symmetric across configurations (no comparison affected), was repaired,
   and all 230 affected instances were re-run (absolute coverage corrected:
   stock 1021 → 1145). Optimal-cost cross-checks between independent planners
   are an effective tripwire for this bug class.
4. **Pre-registration** (§5) converts replicated observations into claims that
   survive reviewer scrutiny; it cost one file and one day.

## 10. Related work

Torralba & Alcázar (SoCS 2013): constrained symbolic search; the mutex-BDD
ordering suggestion we implement. Edelkamp & Kissmann: GAMER. Kissmann &
Hoffmann (JAIR 2014): BDD ordering heuristics for planning; random-ordering
spread; dynamic reordering trade-offs; unpredictability verdict. Torralba et
al.: SymBA*, cGamer. Speck et al.: SymK (ordering inherited unchanged —
verified in source). FORCE/MINCE (Aloul et al.): constraint-graph ordering in
SAT/BDD. SATzilla (Xu et al.) and FD Stone Soup: per-instance selection and
sequential portfolios, the frame for our selector/policy discussion.

## 11. Conclusions and future work

We set out to improve symbolic search and, after falsifying our own first
hypothesis and one popular implicit assumption (that TR size is a good ordering
target), we end with: a **confirmed, pre-registered per-domain improvement to
the IPC-2023 state of the art** (+6 coverage replicated; 1.55× woodworking); a
**bounded suite-wide null** for the always-on variant with its **measured
mechanism**; **ceiling bounds** closing the obvious rescue attempts; and
**methodological artifacts** (noise floors, regression-to-the-mean controls,
duplicate-aware CV, pre-registration) that we believe are as reusable as the
planning results.

Open problems we consider genuinely promising: (a) ordering objectives or
selectors that target **peak search-BDD size** — the operative quantity, which
is bit-deterministic (replicate r = 0.999) but unobservable at preprocessing
time; (b) the **ordering-diversity ceiling**: published random-ordering spreads
suggest heavy good tails that our correlated variants cannot reach; a
bounding experiment (diverse arms + a rerun control on the unsolved boundary,
with a pre-registered gate) is fully designed and costed in the repository.

## Reproducibility

Everything is in this repository and the result CSVs: the planner fork
(`sbd(constraint_order=…, co_weight=…)`, weight-aware optimizer, diagnostics),
the SymK patch, the pre-registration (`PREREG-ab-policy.md`, committed before
data), harnesses (`parallel-bench.py`, `symk_sel_bench.py`, `ab_policy.py` with
interleaving and crash-safe resume), and analysis scripts. Key data:
`ab-policy.csv` (confirmatory A/B), `symk-selector-grand.csv` + fix7 repair
(full-suite SymK), `co-grand-instr.csv` (instrumented mechanism data),
`symk-sweep.csv` (weight sweep). RNG seeds fixed; orderings deterministic.
