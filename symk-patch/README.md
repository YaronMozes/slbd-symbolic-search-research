# SymK patch: constraint-aware variable ordering (the final method)

These files apply over **stock SymK (IPC-2023)** — `src/search/symbolic/` — and
contain the complete final method as evaluated in `REPORT.md`:

- **`opt_order.cc`** — THE method. Two changes vs stock:
  1. After GAMER's causal-graph edges, adds influence edges between every pair
     of SAS⁺ variables whose facts co-occur in a mutex / exactly-one invariant
     group (the `CONSTRAINT_ORDER` block; weight via `SLBD_CO_WEIGHT`,
     activation via `SLBD_CONSTRAINT_ORDER=1`, ablations via `SLBD_CO_ONLY`,
     `SLBD_CO_SKIP_CAUSAL`, `SLBD_CO_NORM`).
  2. The **weight-aware objective fix**: stock GAMER's arrangement objective
     read edge *existence* only and silently ignored weights, in both the full
     evaluation and both incremental swap-delta loops (`w * (j-i)^2` terms).
     With causal-only edges all weights are 1, so stock behaviour is
     bit-identical (verified).
- **`opt_order.h`** — `add_influence` accumulator + weight-aware declarations.

The deployed **per-domain policy** (the confirmed result: +6 coverage in both
pre-registered replicates, woodworking 1.55×) is defined in
`misc/tests/ab_policy.py` (`SIGNAL_DOMAINS` = domain → weight; stock SymK on
all other domains). Experiment drivers: `misc/tests/symk_sel_bench.py`
(full-suite runs; includes the repaired domain-file pairing),
`misc/tests/tr_select_symk.py` (the TR-probe selector studied in §8).

Note: the server copy of SymK additionally carries `TR_SIZE`/probe diagnostics
in `original_state_space.cc` (instrumentation only — no behavioural change);
authoritative copies live on the lab server and in the laptop backup
(`slbd-server-backup/`). The stock baselines for diffing are SymK's public
repository (IPC-2023 version).
