# Pre-registration: per-domain ordering-policy confirmatory A/B

**Registered:** 2026-07-24, BEFORE the experiment runs (commit timestamp = proof).
**Motivation:** across two independent full-scale SymK runs, the constraint-aware
ordering showed replicated per-domain effects: woodworking speedup (w=2 variant,
geomean 0.586, Bonferroni-surviving p=5.7e-6 in the sweep), parking-opt14 +3/0
coverage in both runs, barman-opt11 +1/0 and +2/0, tpp +1/0 twice. This
experiment is the single confirmatory test that either upgrades these replicated
observations to a proven claim or demotes them to "suggestive".

## Design
- **Arms:** `stock` = stock SymK (`sym-bd`, IPC-2023) vs `policy` = SymK with
  constraint-aware ordering, per-domain weight: **w=2.0 on woodworking-opt08/11**,
  **w=1.0 on parking-opt14 / barman-opt11 / tpp**. (On all other IPC domains the
  policy is stock by definition; nothing to run.)
- **Instances:** ALL instances of the 5 signal domains (~120).
- **Replicates:** 2 per (instance, arm) = ~480 runs.
- **Interleaving:** the job queue places stock/policy for the same instance
  adjacently, so machine contention hits both arms symmetrically (within-run
  contrast floor ±3–4, vs ±17 across runs).
- **Protocol:** 1800 s wall, 8 GB, single CPU per run; fixed domain pairing
  (all 5 domains use a shared domain.pddl — unaffected by the fix7 bug class).

## Pre-registered endpoints and gates
1. **PRIMARY — coverage contrast:** (policy solves − stock solves) summed over
   the 5 domains, averaged over the 2 replicates.
   **GATE: contrast ≥ +4 → CONFIRMED** (per-domain policy improves SymK
   coverage). Contrast < +4 → reported as *suggestive only*, no coverage claim.
2. **SECONDARY — woodworking speed:** geomean wall ratio policy/stock on
   commonly-solved woodworking instances, pooled over replicates.
   **GATE: ratio ≤ 0.85 → CONFIRMED speedup** (claim the sweep's 0.586 ± CI as
   the effect size; this run as its confirmation). Ratio > 0.85 → demote to
   single-sweep evidence.
3. **GUARD — no-harm:** policy must not lose net coverage on any signal domain
   in both replicates (a replicated per-domain loss voids that domain's claim).
4. **Cost integrity:** any cost mismatch between arms on a commonly-solved
   instance voids the experiment (expected: zero).

No other endpoints will be claimed from this data; results land in
`slbd-results/ab-policy.csv` on the server. (Correction, added with the
results: no analysis script was committed alongside this document, contrary
to an earlier wording here. The gates above are computed by
`misc/analysis/check_gates.py`, added later. This document was pushed
publicly ~16 h before the results commit and its gates were never edited,
which fixes the criteria publicly in advance but does not by itself prove
the runs had not started.)

---
## RESULTS (2026-07-25, gates applied exactly as registered — analysis unchanged)

- **GATE 4 integrity: PASS** — 0 cost mismatches on all commonly-solved pairs.
- **GATE 1 PRIMARY: CONFIRMED** — coverage contrast **+6 in replicate 1 and +6 in
  replicate 2** (average +6.0 ≥ +4). Per-domain, policy − stock:
  parking-opt14 +2/+3, barman-opt11 +1/+2, tpp +1/+1, woodworking-opt08 +1/+0,
  woodworking-opt11 +1/+0. No negative cell anywhere.
- **GATE 2 SECONDARY: CONFIRMED** — woodworking wall geomean policy/stock =
  **0.647** (n=94 commonly-solved pairs, policy faster on 70/94) ≤ 0.85.
- **GATE 3 GUARD: PASS** — zero per-domain net losses in any replicate.

**Confirmed claim:** the per-domain constraint-ordering policy improves stock
SymK (IPC-2023) by **+6 coverage** on the five signal domains (exactly
replicated across two interleaved replicates) and accelerates woodworking by
**~1.55×**, with optimality preserved. Suite-wide, the policy equals stock on
all other domains by construction.

**fix7-corrected grand-run absolutes** (230 repaired instances merged):
stock 1145 / always-on 1139 / selector 1150, of 1847. The suite-wide null for
the always-on ordering is unchanged (−6, within the ±17 replicate floor).
