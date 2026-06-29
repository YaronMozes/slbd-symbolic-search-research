#!/usr/bin/env python3
"""Find a cheap setup-time feature that predicts the fastest ordering.

Reads a co-ablation CSV (domain,problem,config,solved,cost,search_time,
mutex_nodes,sas_vars,co_edges) with configs causal/constraint-only/combined.
Computes per-instance the best config + features, and evaluates simple rules
for an intelligent selector (no need to run all three orderings).
"""
import csv
import math
import sys
from collections import defaultdict

CFGS = ["causal", "constraint-only", "combined"]
path = sys.argv[1]

rows = list(csv.DictReader(open(path)))
inst = defaultdict(dict)
for r in rows:
    inst[(r["domain"], r["problem"])][r["config"]] = r


def fnum(x):
    try:
        return float(x)
    except (ValueError, TypeError):
        return None


# per-instance records where all three solved (for clean comparison)
recs = []
for key, m in inst.items():
    if not all(c in m and m[c]["solved"] == "1" and m[c]["search_time"]
               for c in CFGS):
        continue
    t = {c: float(m[c]["search_time"]) for c in CFGS}
    best = min(CFGS, key=lambda c: t[c])
    rec = {
        "domain": key[0], "t": t, "best": best,
        "sas": fnum(m["combined"].get("sas_vars")),
        "edges": fnum(m["combined"].get("co_edges")),
        "mx_causal": fnum(m["causal"].get("mutex_nodes")),
        "mx_co": fnum(m["constraint-only"].get("mutex_nodes")),
        "mx_comb": fnum(m["combined"].get("mutex_nodes")),
    }
    recs.append(rec)

print("Instances where all 3 solved: %d" % len(recs))

# 1. how often each config is the per-instance best
cnt = defaultdict(int)
for r in recs:
    cnt[r["best"]] += 1
print("Per-instance best: " + ", ".join("%s=%d" % (c, cnt[c]) for c in CFGS))


def gm(xs):
    xs = [x for x in xs if x and x > 0]
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else float("nan")


# 2. oracle vs each single config (geomean of t/causal)
print("\nGeomean search-time vs causal (lower=better):")
for c in CFGS:
    print("  %-16s %.3f" % (c, gm([r["t"][c] / r["t"]["causal"] for r in recs])))
print("  %-16s %.3f" % ("ORACLE(best)",
                        gm([min(r["t"].values()) / r["t"]["causal"] for r in recs])))

# 3. feature view: when is constraint-only the best (or much better than causal)?
print("\nconstraint-only vs causal by feature buckets (ratio co/causal):")
for r in recs:
    r["ratio_co"] = r["t"]["constraint-only"] / r["t"]["causal"]
    r["edges_per_var"] = (r["edges"] / r["sas"]) if (r["edges"] and r["sas"]) else 0
    r["mx_reduction"] = (r["mx_co"] / r["mx_causal"]) if (r["mx_causal"] and r["mx_causal"] > 0) else 1.0

# bucket by edges_per_var
recs_sorted = sorted(recs, key=lambda r: r["edges_per_var"])
print("  by edges/var:")
for r in recs_sorted:
    flag = "WIN" if r["ratio_co"] < 0.95 else ("LOSE" if r["ratio_co"] > 1.1 else "~")
    print("    %-22s edges/var=%6.1f mx_red=%.2f  co/causal=%.2f %s" % (
        r["domain"], r["edges_per_var"], r["mx_reduction"], r["ratio_co"], flag))

# 4. evaluate simple selector rules (oracle-capture)
print("\nSelector rule evaluation (geomean vs causal; 1.0 = no better than GAMER):")


def eval_rule(name, pick):
    ratios = []
    for r in recs:
        c = pick(r)
        ratios.append(r["t"][c] / r["t"]["causal"])
    print("  %-40s %.3f" % (name, gm(ratios)))


eval_rule("always causal (GAMER)", lambda r: "causal")
eval_rule("always combined", lambda r: "combined")
eval_rule("always constraint-only", lambda r: "constraint-only")
# candidate intelligent rules:
eval_rule("constraint-only if edges/var<5 else combined",
          lambda r: "constraint-only" if r["edges_per_var"] < 5 else "combined")
eval_rule("constraint-only if mx_reduction<0.3 else combined",
          lambda r: "constraint-only" if r["mx_reduction"] < 0.3 else "combined")
eval_rule("ORACLE (cheat)", lambda r: r["best"])
