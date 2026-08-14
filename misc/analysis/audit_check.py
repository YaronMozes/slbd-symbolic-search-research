"""Verify Codex's two blocking findings."""
import csv, os, math
from collections import defaultdict
D = r"C:\Users\Yaron\Desktop\slbd-server-backup\results-2026-07-24"
def load(n): return list(csv.DictReader(open(os.path.join(D, n), encoding="utf-8", errors="replace")))

# merged corrected suite ("run B")
grand = load("symk-selector-grand.csv"); fix = load("symk-selector-grand-fix7.csv")
fixed = {(r["domain"], r["problem"]) for r in fix}
B = defaultdict(dict)
for r in grand:
    if (r["domain"], r["problem"]) not in fixed: B[(r["domain"], r["problem"])][r["config"]] = r
for r in fix: B[(r["domain"], r["problem"])][r["config"]] = r
# independent earlier full-suite A/B ("run A")
A = defaultdict(dict)
for r in load("symk-co-grand-ab.csv"): A[(r["domain"], r["problem"])][r["config"]] = r
sv = lambda m, c: m.get(c, {}).get("solved") == "1"

def domgain(M):
    g = defaultdict(int)
    for (d, p), m in M.items():
        if "off" in m and "on" in m:
            g[d] += (1 if sv(m, "on") else 0) - (1 if sv(m, "off") else 0)
    return g

gA, gB = domgain(A), domgain(B)
print("=== BLOCKING 2: is +6 just what the selection procedure produces? ===")
for src, dst, ln, dn in ((gA, gB, "A", "B"), (gB, gA, "B", "A")):
    top5 = sorted(src, key=lambda d: -src[d])[:5]
    print("  pick top-5 by run %s -> %s" % (ln, ", ".join("%s(%+d)" % (d, src[d]) for d in top5)))
    print("     their combined gain measured in run %s: %+d" % (dn, sum(dst.get(d, 0) for d in top5)))
paper5 = ["parking-opt14-strips", "barman-opt11-strips", "tpp",
          "woodworking-opt08-strips", "woodworking-opt11-strips"]
print("  paper's five, gain in run A: %+d ; in run B: %+d"
      % (sum(gA.get(d, 0) for d in paper5), sum(gB.get(d, 0) for d in paper5)))
# null: how often do 5 random domains give >= +6 in the other run?
import random
random.seed(7)
doms = [d for d in gB if d in gA]
hits = 0
for _ in range(20000):
    s = random.sample(doms, 5)
    if sum(gB.get(d, 0) for d in s) >= 6: hits += 1
print("  random 5 domains reaching >= +6 in run B: %.2f%%" % (100.0 * hits / 20000))

print("\n=== BLOCKING 1: the +-17 floor and the claimed drift of 26 ===")
fixdoms = {k[0] for k in fixed}
covA = sum(1 for k, m in A.items() if k[0] not in fixdoms and sv(m, "off"))
covB = sum(1 for k, m in B.items() if k[0] not in fixdoms and sv(m, "off"))
sharedA = {k for k, m in A.items() if "off" in m}
sharedB = {k for k, m in B.items() if "off" in m}
print("  stock coverage excl. repaired domains:  run A=%d  run B=%d  -> drift %d"
      % (covA, covB, abs(covA - covB)))
print("  instances with an 'off' row: run A=%d  run B=%d  (same instance set? %s)"
      % (len(sharedA), len(sharedB), sharedA == sharedB))
common = sharedA & sharedB
ca = sum(1 for k in common if sv(A[k], "off")); cb = sum(1 for k in common if sv(B[k], "off"))
print("  on the %d instances present in BOTH: A=%d B=%d -> drift %d" % (len(common), ca, cb, abs(ca - cb)))
flip = sum(1 for k in common if sv(A[k], "off") != sv(B[k], "off"))
print("  instances where identical stock config disagrees with itself: %d" % flip)

print("\n=== two-ordering oracle after the fix7 merge ===")
onon = sum(1 for m in B.values() if sv(m, "on") and not sv(m, "off"))
offon = sum(1 for m in B.values() if sv(m, "off") and not sv(m, "on"))
union = sum(1 for m in B.values() if sv(m, "off") or sv(m, "on"))
off = sum(1 for m in B.values() if sv(m, "off"))
print("  on-only=%d off-only=%d union=%d stock=%d -> oracle gain %+d  [paper says +12]"
      % (onon, offon, union, off, union - off))
