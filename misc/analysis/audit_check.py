"""Selection-null checks and the two-ordering oracle, from released data only.

Run A = data/symk-co-grand-ab.csv (earlier independent off/on full-suite run;
affected by the pairing defect, so use only for WITHIN-run contrasts).
Run B = the merged corrected suite behind Table 3.

Caveat printed with the results: selecting "top five domains by coverage gain"
involves ties at the cutoff, so the cross-run sums depend on tie-breaking; we
report the tie-exhausted range, not a single arbitrary order. And no variant of
this check is a complete post-selection null -- the real selection also saw
runtime and sweep evidence (see the paper's Limitations)."""
import itertools
import random
from collections import defaultdict

from common import load, merged_suite, solved

A = defaultdict(dict)
for r in load("symk-co-grand-ab.csv"):
    A[(r["domain"], r["problem"])][r["config"]] = r
B = merged_suite()


def domgain(M):
    g = defaultdict(int)
    for (d, p), m in M.items():
        if "off" in m and "on" in m:
            g[d] += (1 if solved(m, "on") else 0) - (1 if solved(m, "off") else 0)
    return g


gA, gB = domgain(A), domgain(B)

def top5_range(src, dst):
    """All top-5 selections consistent with ties at the cutoff; returns the
    set of cross-run sums."""
    vals = sorted(set(src.values()), reverse=True)
    chosen, remaining = [], 5
    for v in vals:
        tier = [d for d in src if src[d] == v]
        if len(tier) <= remaining:
            chosen += tier
            remaining -= len(tier)
            if remaining == 0:
                return {sum(dst.get(d, 0) for d in chosen)}
        else:
            base = sum(dst.get(d, 0) for d in chosen)
            sums = {base + sum(dst.get(d, 0) for d in combo)
                    for combo in itertools.combinations(tier, remaining)}
            return sums
    return {sum(dst.get(d, 0) for d in chosen)}


print("=== top-5-by-one-run measured in the other (tie-exhausted range) ===")
print("  by run A -> in run B: %s" % sorted(top5_range(gA, gB)))
print("  by run B -> in run A: %s" % sorted(top5_range(gB, gA)))
paper5 = ["parking-opt14-strips", "barman-opt11-strips", "tpp",
          "woodworking-opt08-strips", "woodworking-opt11-strips"]
print("  paper's five: %+d in run A, %+d in run B" % (
    sum(gA.get(d, 0) for d in paper5), sum(gB.get(d, 0) for d in paper5)))

random.seed(7)
doms = [d for d in gB if d in gA]
for target in (5, 6):
    hits = sum(1 for _ in range(20000)
               if sum(gB.get(d, 0) for d in random.sample(doms, 5)) >= target)
    print("  random 5 domains reaching >= +%d in run B: %.2f%%" % (
        target, 100.0 * hits / 20000))
print("  (uniform-random draws are NOT a complete post-selection null; the")
print("   actual selection also used runtime and sweep evidence)")

print()
print("=== two-ordering oracle on the corrected suite ===")
onon = sum(1 for m in B.values() if solved(m, "on") and not solved(m, "off"))
offon = sum(1 for m in B.values() if solved(m, "off") and not solved(m, "on"))
union = sum(1 for m in B.values() if solved(m, "off") or solved(m, "on"))
off = sum(1 for m in B.values() if solved(m, "off"))
print("  on-only=%d off-only=%d union=%d stock=%d -> oracle %+d  [paper: +13]"
      % (onon, offon, union, off, union - off))
