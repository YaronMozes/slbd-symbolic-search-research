"""Regenerates Tables 1-3: the pre-registered gates, the per-domain A/B
breakdown, and the suite-wide comparison including the domain-clustered
bootstrap confidence intervals."""
import random
from collections import defaultdict

from common import load, merged_suite, fnum, solved, gm, median

# ---------------------------------------------------------------- Tables 1-2
rows = load("ab-policy.csv")
DOMS = ["parking-opt14-strips", "barman-opt11-strips", "tpp",
        "woodworking-opt08-strips", "woodworking-opt11-strips"]
inst = defaultdict(dict)
for r in rows:
    inst[(r["domain"], r["problem"], r["rep"])][r["config"]] = r

print("=" * 66)
print("TABLE 2: per-domain breakdown (stock R1/R2 -> policy R1/R2)")
print("=" * 66)
tot = defaultdict(int)
for d in DOMS:
    cells = {}
    for cfg in ("stock", "policy"):
        for rep in ("1", "2"):
            n = sum(1 for k, m in inst.items()
                    if k[0] == d and k[2] == rep and solved(m, cfg))
            cells[(cfg, rep)] = n
            tot[(cfg, rep)] += n
    print("  %-28s %2d/%2d -> %2d/%2d  (delta %+d/%+d)" % (
        d, cells[("stock", "1")], cells[("stock", "2")],
        cells[("policy", "1")], cells[("policy", "2")],
        cells[("policy", "1")] - cells[("stock", "1")],
        cells[("policy", "2")] - cells[("stock", "2")]))
print("  %-28s %2d/%2d -> %2d/%2d" % (
    "TOTAL", tot[("stock", "1")], tot[("stock", "2")],
    tot[("policy", "1")], tot[("policy", "2")]))

print()
print("=" * 66)
print("TABLE 1: pre-registered gates")
print("=" * 66)
c1 = tot[("policy", "1")] - tot[("stock", "1")]
c2 = tot[("policy", "2")] - tot[("stock", "2")]
print("  GATE 1 coverage contrast >= +4 : %+d / %+d  -> %s" % (
    c1, c2, "CONFIRMED" if (c1 + c2) / 2.0 >= 4 else "not met"))

wood = [(fnum(m["policy"], "wall"), fnum(m["stock"], "wall"))
        for k, m in inst.items() if k[0].startswith("woodworking")
        and solved(m, "stock") and solved(m, "policy")]
ratios = [a / b for a, b in wood if a and b]
faster = sum(1 for x in ratios if x < 1)
print("  GATE 2 woodworking geomean <= 0.85 : %.3f (n=%d, faster on %d)  -> %s" % (
    gm(ratios), len(ratios), faster,
    "CONFIRMED" if gm(ratios) <= 0.85 else "not met"))

loss = 0
for d in DOMS:
    for rep in ("1", "2"):
        s = sum(1 for k, m in inst.items() if k[0] == d and k[2] == rep and solved(m, "stock"))
        p = sum(1 for k, m in inst.items() if k[0] == d and k[2] == rep and solved(m, "policy"))
        loss += (p < s)
print("  GATE 3 no per-domain loss in any replicate : %d losses  -> %s" % (
    loss, "PASS" if loss == 0 else "violated"))

mm = comp = 0
for m in inst.values():
    if solved(m, "stock") and solved(m, "policy"):
        comp += 1
        if m["stock"]["cost"] != m["policy"]["cost"]:
            mm += 1
print("  GATE 4 cost agreement : %d disagreements / %d comparable pairs  -> %s" % (
    mm, comp, "PASS" if mm == 0 else "violated"))
print("  (cost agreement between arms; not an independent validation of optimality)")

# ---------------------------------------------------------------- Table 3
print()
print("=" * 66)
print("TABLE 3: suite-wide (merged corrected dataset)")
print("=" * 66)
M = merged_suite()
random.seed(11)
for cfg in ("off", "on", "selector"):
    cov = sum(1 for m in M.values() if solved(m, cfg))
    line = "  %-9s coverage=%4d" % (cfg, cov)
    if cfg != "off":
        pairs = [m for m in M.values() if solved(m, "off") and solved(m, cfg)]
        rs, sr, byd = [], [], defaultdict(list)
        for k, m in M.items():
            if not (solved(m, "off") and solved(m, cfg)):
                continue
            a, b = fnum(m["off"], "wall"), fnum(m[cfg], "wall")
            if a and b:
                rs.append(b / a)
                byd[k[0]].append(b / a)
            sa, sb = fnum(m["off"], "search_time"), fnum(m[cfg], "search_time")
            if sa and sb:
                sr.append(sb / sa)
        dl = list(byd.values())
        boots = []
        for _ in range(2000):
            samp = [x for _ in range(len(dl)) for x in random.choice(dl)]
            boots.append(gm(samp))
        boots.sort()
        lo, hi = boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots))]
        line += ("  wall gm=%.4f med=%.4f CI=[%.2f,%.2f] n=%d  (search-only gm=%.3f)"
                 % (gm(rs), median(rs), lo, hi, len(rs), gm(sr)))
    print(line)


if __name__ == "__main__":
    pass
