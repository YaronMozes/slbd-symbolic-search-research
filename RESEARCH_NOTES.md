# SLBD Research Notes — Symbolic Bidirectional Search Guidance

**Status as of 2026-06-27.** Audience: project teammate getting up to speed.

This branch (`symbolic-landmark-guidance`) explores whether we can improve
Fast Downward's **symbolic bidirectional search** (`sbd`, BDD-based blind
uniform-cost search). The original hypothesis was *landmark-guided direction
selection* (`slbd`). After rigorous measurement, that hypothesis — and five
further ideas — are **falsified**. This doc explains what we tried, what the
data showed, and where it leaves us.

---

## TL;DR

- The original `slbd` source had been corrupted (88× `return`→`retun`); it did
  not compile and the committed binary was stale. **Fixed and rebuilt.**
- We built a rigorous measurement harness (the **oracle** presets in
  `compare-slbd.py`) and a per-decision frontier trace (`SLBD_TRACE_FRONTIER`).
- **Six idea-families were measured and all fail to beat baseline `sbd`:**
  1. Landmark direction guidance (coverage/agenda/meeting) — net-negative.
  2. Meet-in-the-middle BDD signals (`meet_bdd`, `balance_bdd`) — inert/harmful.
  3. Trend-based forward/backward budget control — no headroom (split already near-optimal).
  4. Forward-reachability pruning of the backward search — self-defeating.
  5. Stronger generic pruning / heuristics — = SymBA*/abstractions = known work.
  6. Beyond-h² acyclicity pruning (blocks) — sound + optimal but BDD-hostile (net loss).
- **Root cause (consistent across all six):** node-count BDD-size direction
  selection is robust and near-optimal; the binding constraint is *intrinsic
  BDD blow-up* (the BDD contents), which none of these decisions can escape.
- **Strongest current deliverable:** a rigorous *characterization / negative
  result* with original methodology. Best-odds *positive* lead not yet tried:
  domain-specific BDD **variable ordering** (directly shrinks BDDs).

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
