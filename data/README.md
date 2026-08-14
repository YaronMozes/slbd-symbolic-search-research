# Data behind the paper

The five datasets every claim in [`../paper/main.pdf`](../paper/main.pdf) rests on.
All runs: **1800 s wall-clock, 8 GB memory, 1 CPU core per task**, on a 64-core
Linux server (multiple independent single-core runs executed concurrently).
Additional intermediate runs from the wider campaign are kept out of the
repository; these are the files the reported numbers come from.

| File | Rows | Supports |
|---|---|---|
| `ab-policy.csv` | 480 | **Tables 1–2** — the pre-registered confirmatory A/B (stock vs per-domain policy, 5 signal domains, 2 replicates) |
| `symk-selector-grand.csv` | 5,541 | **Table 3** — full IPC suite, 1,847 instances × {off, on, selector} |
| `symk-selector-grand-fix7.csv` | 690 | Repair run for 230 instances: seven IPC domains ship one domain file per instance, and an early version of our driver mis-paired them, so those runs failed identically in every configuration. Merge this file over the one above (replacing those instances' rows) to obtain the coverage reported in the paper |
| `co-grand-instr.csv` | 7,388 | **§4.3 mechanism** — instrumented run with per-instance BDD statistics |
| `symk-sweep.csv` | 2,292 | **§4.1 dose–response** — constraint-weight sweep (off / on / w0.5 / w2 / norm / skipc) |
| `symk-co-grand-ab.csv` | 3,694 | Earlier independent full-suite off/on run ("discovery run A"). **Caution:** affected by the domain-pairing defect and never repaired — use only for within-run contrasts and selection-null checks, never for absolute coverage |

## Columns

**`ab-policy.csv`** — `domain, problem, config, rep, solved, cost, wall, search_time`
where `config ∈ {stock, policy}` and `rep ∈ {1, 2}`.

**`symk-selector-grand.csv`, `symk-selector-grand-fix7.csv`** —
`domain, problem, config, solved, cost, wall, search_time, picked, tr_off, tr_on, probe_time`
where `config ∈ {off, on, selector}`. `wall` includes all selector probe
overhead and is the honest end-to-end metric; `search_time` excludes it.

**`co-grand-instr.csv`** — `domain, problem, config, solved, cost, search_time,`
`mutex_nodes, sas_vars, co_edges, tr_nodes, fw_expanded, bw_expanded,`
`fw_closed_nodes, bw_closed_nodes, peak_bdd_nodes, final_bdd_nodes,`
`fw_steps, bw_steps, fw_truncated, bw_truncated`. Configs are `causal`
(our fork's GAMER baseline), `comb-w` (constraint-aware), `tr-select`, and
`symk` (stock SymK reference). BDD statistics are recorded for `causal` and
`comb-w` only. `co_edges = 0` marks instances where the method is a structural
no-op. Expanded-state counts come from BDD model counting and can be very large
(e.g. 1e30) — that is a valid state count, not a parsing error.

## Reproducing the headline numbers

To reproduce the corrected suite-wide coverage of Table 3, replace each
instance's rows in `symk-selector-grand.csv` with its rows from
`symk-selector-grand-fix7.csv` (230 instances), then count `solved == 1` per
config: **off 1145, on 1139, selector 1150** of 1,847.

The pre-registered gates are evaluated directly on `ab-policy.csv`; the
procedure and thresholds are fixed in [`../PREREG-ab-policy.md`](../PREREG-ab-policy.md),
which was committed before these data existed.
