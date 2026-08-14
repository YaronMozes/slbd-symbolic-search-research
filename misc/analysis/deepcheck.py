"""Re-derives the Results/Methodology figures from the released data.
Expected values in brackets are the ones printed in the current paper."""
import hashlib
import os
from collections import defaultdict

from common import DATA, load, merged_suite, fnum, solved, gm, median, sign_test

M = merged_suite()

print("=" * 62)
print("SECTION 4.2")
print("=" * 62)
fc = {k: m for k, m in M.items() if k[0] == "freecell"}
print("freecell off=%d on=%d   [paper: 25 -> 20]" % (
    sum(1 for m in fc.values() if solved(m, "off")),
    sum(1 for m in fc.values() if solved(m, "on"))))
ft = [m for k, m in M.items() if k[0] == "floortile-opt14-strips"
      and solved(m, "off") and solved(m, "on")]
rs = [fnum(m["on"], "wall") / fnum(m["off"], "wall")
      for m in ft if fnum(m["off"], "wall") and fnum(m["on"], "wall")]
slower = sum(1 for x in rs if x > 1)
p = sign_test(slower, len(rs) - slower)
print("floortile-opt14 median=%.3f slower %d/%d  Bonferroni(66) p=%.2g"
      "   [paper: 1.56x, 20/20, 1.3e-4]" % (median(rs), slower, len(rs), min(1, p * 66)))

print()
print("=" * 62)
print("NOISE (4.4): identical-code-path timing pairs")
print("=" * 62)
import math
pairs = [(fnum(m["selector"], "search_time"), fnum(m["off"], "search_time"))
         for m in M.values() if solved(m, "off") and solved(m, "selector")
         and (m["selector"].get("picked") or "") in ("off", "presolve")
         and fnum(m["selector"], "search_time") and fnum(m["off"], "search_time")]
lr = [math.log(a / b) for a, b in pairs]
sd = math.sqrt(sum(x * x for x in lr) / len(lr))
big = sum(1 for a, b in pairs if abs(a / b - 1) > 0.20)
print("n=%d sd(log)=%.3f  >20%% apart: %.0f%%   [paper: 1,076 / 0.231 / 33%%]"
      % (len(pairs), sd, 100.0 * big / len(pairs)))

print()
print("=" * 62)
print("REGRESSION TO THE MEAN (4.5.1), wall, threshold 300s")
print("=" * 62)
common = [m for m in M.values() if solved(m, "off") and solved(m, "on")
          and fnum(m["off"], "wall") and fnum(m["on"], "wall")]
for lab, sel in (("by-off  >300s", lambda m: fnum(m["off"], "wall") > 300),
                 ("by-on   >300s", lambda m: fnum(m["on"], "wall") > 300),
                 ("symmetric>300s", lambda m: min(fnum(m["off"], "wall"),
                                                  fnum(m["on"], "wall")) > 300)):
    sub = [m for m in common if sel(m)]
    r = [fnum(m["on"], "wall") / fnum(m["off"], "wall") for m in sub]
    print("  %-15s n=%3d geomean=%.3f faster=%.0f%%" % (
        lab, len(sub), gm(r), 100.0 * sum(1 for x in r if x < 1) / len(r)))
print("  [paper: 0.803 / 1.096 / 0.919 with 54%]")

print()
print("=" * 62)
print("IPC DUPLICATES (4.5.2): needs a downward-benchmarks checkout")
print("=" * 62)
BM = os.environ.get("BENCHMARKS", "")
if BM and os.path.isdir(BM):
    doms = {k[0] for k in M}
    h2f = defaultdict(list)
    for dom in doms:
        pdir = os.path.join(BM, dom)
        if not os.path.isdir(pdir):
            continue
        for fn in os.listdir(pdir):
            if not fn.endswith(".pddl") or "domain" in fn:
                continue
            try:
                h = hashlib.md5(open(os.path.join(pdir, fn), "rb").read()).hexdigest()
            except OSError:
                continue
            h2f[h].append((dom, fn))
    cross = {h: v for h, v in h2f.items() if len({d for d, _ in v}) > 1}
    print("cross-domain duplicate sets: %d ; instances: %d   [paper: 155 / 322]"
          % (len(cross), sum(len(v) for v in cross.values())))
else:
    print("set BENCHMARKS=/path/to/downward-benchmarks to run this check")
