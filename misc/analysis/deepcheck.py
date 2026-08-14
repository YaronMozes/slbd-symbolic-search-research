"""Deep verification of every checkable number in paper/main.tex."""
import csv, math, os, hashlib
from collections import defaultdict

D = r"C:\Users\Yaron\Desktop\slbd-server-backup\results-2026-07-24"
BM = r"C:\Users\Yaron\Desktop\Planning\downward-benchmarks"

def load(n):
    return list(csv.DictReader(open(os.path.join(D, n), encoding="utf-8", errors="replace")))
def gm(xs):
    xs = [x for x in xs if x and x > 0]
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else float("nan")
def med(xs):
    xs = sorted(x for x in xs if x is not None)
    return xs[len(xs) // 2] if xs else float("nan")
def f(r, k):
    try:
        v = float(r[k]); return v if v > 0 else None
    except Exception:
        return None
def signtest(a, b):
    n = a + b; k = min(a, b)
    if n == 0: return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)

grand = load("symk-selector-grand.csv"); fix = load("symk-selector-grand-fix7.csv")
fixed = {(r["domain"], r["problem"]) for r in fix}
M = defaultdict(dict)
for r in grand:
    if (r["domain"], r["problem"]) not in fixed: M[(r["domain"], r["problem"])][r["config"]] = r
for r in fix: M[(r["domain"], r["problem"])][r["config"]] = r
sv = lambda m, c: m.get(c, {}).get("solved") == "1"

print("=" * 62); print("CLAIMS IN SECTION 4.2"); print("=" * 62)
fc = {k: m for k, m in M.items() if k[0] == "freecell"}
print("freecell off=%d on=%d   [paper: 25 -> 20]" % (
    sum(1 for m in fc.values() if sv(m, "off")), sum(1 for m in fc.values() if sv(m, "on"))))
ft = [m for k, m in M.items() if k[0].startswith("floortile") and sv(m, "off") and sv(m, "on")]
rs = [f(m["on"], "wall") / f(m["off"], "wall") for m in ft if f(m["off"], "wall") and f(m["on"], "wall")]
slower = sum(1 for x in rs if x > 1)
print("floortile median=%.3f geomean=%.3f  slower on %d/%d  [paper: 1.56x on 20/20]" % (
    med(rs), gm(rs), slower, len(rs)))
print("  sign test p=%.2g, Bonferroni(x66)=%.3g  [paper: 0.005]" % (
    signtest(slower, len(rs) - slower), min(1.0, signtest(slower, len(rs) - slower) * 66)))

print(); print("=" * 62); print("NOISE FLOOR (section 4.4)"); print("=" * 62)
# selector rows that ran the stock arm = identical code path to off -> pure noise
pairs = [(f(m["selector"], "search_time"), f(m["off"], "search_time")) for m in M.values()
         if sv(m, "off") and sv(m, "selector")
         and (m["selector"].get("picked") or "") in ("off", "presolve")
         and f(m["selector"], "search_time") and f(m["off"], "search_time")]
lr = [math.log(a / b) for a, b in pairs]
sd = math.sqrt(sum(x * x for x in lr) / len(lr)) if lr else float("nan")
big = sum(1 for a, b in pairs if abs(a / b - 1) > 0.20)
print("same-code-path pairs n=%d  sd(log ratio)=%.3f  [paper: 290 pairs, 0.196]" % (len(pairs), sd))
print("  differ by >20%%: %d (%.0f%%)  [paper: 29%%]" % (big, 100.0 * big / len(pairs)))

print(); print("=" * 62); print("REGRESSION TO THE MEAN (section 4.5.1)"); print("=" * 62)
common = [m for m in M.values() if sv(m, "off") and sv(m, "on")
          and f(m["off"], "wall") and f(m["on"], "wall")]
for label, sel in (("select hard by OFF >10s", lambda m: f(m["off"], "wall") > 10),
                   ("select hard by ON  >10s", lambda m: f(m["on"], "wall") > 10),
                   ("symmetric min(on,off)>10s", lambda m: min(f(m["off"], "wall"), f(m["on"], "wall")) > 10)):
    sub = [m for m in common if sel(m)]
    r = [f(m["on"], "wall") / f(m["off"], "wall") for m in sub]
    print("  %-26s n=%4d geomean=%.3f  on-faster %.1f%%" % (
        label, len(sub), gm(r), 100.0 * sum(1 for x in r if x < 1) / len(r)))
print("  [paper: 0.796 / 1.064 / 0.908 with 54.5%% win rate]")

print(); print("=" * 62); print("TR-PROBE SELECTOR (section 4.4)"); print("=" * 62)
dev = [m for m in M.values() if (m.get("selector", {}).get("picked") or "") == "on"
       and sv(m, "off") and sv(m, "on") and f(m["off"], "search_time") and f(m["on"], "search_time")]
right = sum(1 for m in dev if f(m["on"], "search_time") < f(m["off"], "search_time"))
print("  deviating picks n=%d, right %d (%.1f%%)  p=%.2g  [paper: 70.8%%, p=5.4e-4]" % (
    len(dev), right, 100.0 * right / len(dev) if dev else 0, signtest(right, len(dev) - right)))

print(); print("=" * 62); print("RESTART-SCHEDULE CLAIMS (section 4.4)"); print("=" * 62)
resc = [m for m in M.values() if not sv(m, "off") and sv(m, "on") and f(m["on"], "wall")]
print("  stock-unsolved rescued by 'on' within 600s: %d   [paper: zero]" %
      sum(1 for m in resc if f(m["on"], "wall") <= 600))
print("  ... within 900s: %d   [paper: 3]" % sum(1 for m in resc if f(m["on"], "wall") <= 900))
print("  solved instances needing >900s (off): %d   [paper: 48]" %
      sum(1 for m in M.values() if sv(m, "off") and f(m["off"], "wall") and f(m["off"], "wall") > 900))

print(); print("=" * 62); print("SCOPE: no-op instances (section 4.3 item 5)"); print("=" * 62)
gi = load("co-grand-instr.csv")
ci = defaultdict(dict)
for r in gi: ci[(r["domain"], r["problem"])][r["config"]] = r
zero = tot = 0; domzero = defaultdict(lambda: [0, 0])
for k, m in ci.items():
    w = m.get("comb-w")
    if not w or w.get("co_edges", "") == "": continue
    tot += 1; z = float(w["co_edges"]) == 0
    zero += z; domzero[k[0]][0] += z; domzero[k[0]][1] += 1
alldom = sum(1 for d, (z, t) in domzero.items() if z == t)
print("  co_edges==0: %d/%d = %.1f%%  [paper: 25.6%%]" % (zero, tot, 100.0 * zero / tot))
print("  fully-placebo domains: %d of %d  [paper: 13 of 66]" % (alldom, len(domzero)))

print(); print("=" * 62); print("IPC CROSS-YEAR DUPLICATES (section 4.5.2)"); print("=" * 62)
if os.path.isdir(BM):
    h2f = defaultdict(list)
    for dom in os.listdir(BM):
        p = os.path.join(BM, dom)
        if not os.path.isdir(p) or dom.startswith("."): continue
        for fn in os.listdir(p):
            if not fn.endswith(".pddl") or "domain" in fn: continue
            try:
                h = hashlib.md5(open(os.path.join(p, fn), "rb").read()).hexdigest()
            except Exception:
                continue
            h2f[h].append((dom, fn))
    dupsets = {h: v for h, v in h2f.items() if len(v) > 1}
    cross = {h: v for h, v in dupsets.items() if len({d for d, _ in v}) > 1}
    ndup = sum(len(v) for v in cross.values())
    print("  cross-domain duplicate SETS: %d   [paper: 123 sets]" % len(cross))
    print("  instances involved: %d   [paper: 299]" % ndup)
    print("  (bit-identical by md5; paper cites 108 bit-identical targets)")
else:
    print("  benchmark dir not found - skipped")
