"""Mechanism figure: TR size is a poor proxy for runtime; peak BDD size is not."""
import csv, math, os
from collections import defaultdict
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SRC = r"C:\Users\Yaron\Desktop\slbd-server-backup\results-2026-07-24\co-grand-instr.csv"
OUT = r"c:\Users\Yaron\Desktop\Planning\fast-downward-symbolic\paper\mechanism.pdf"

rows = list(csv.DictReader(open(SRC)))
inst = defaultdict(dict)
for r in rows:
    inst[(r["domain"], r["problem"])][r["config"]] = r

def f(r, k):
    try:
        v = float(r[k]); return v if v > 0 else None
    except Exception:
        return None

tr, pk, tm = [], [], []
for m in inst.values():
    c, w = m.get("causal"), m.get("comb-w")
    if not c or not w or c["solved"] != "1" or w["solved"] != "1":
        continue
    try:
        if float(w.get("co_edges") or 0) <= 0:      # method inactive
            continue
    except Exception:
        continue
    a, b = f(c, "tr_nodes"), f(w, "tr_nodes")
    pa, pb = f(c, "peak_bdd_nodes"), f(w, "peak_bdd_nodes")
    sa, sb = f(c, "search_time"), f(w, "search_time")
    if None in (a, b, pa, pb, sa, sb):
        continue
    tr.append(math.log(b / a)); pk.append(math.log(pb / pa)); tm.append(math.log(sb / sa))
tr, pk, tm = np.array(tr), np.array(pk), np.array(tm)

def spearman(x, y):
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    rx -= rx.mean(); ry -= ry.mean()
    return float((rx * ry).sum() / math.sqrt((rx ** 2).sum() * (ry ** 2).sum()))

rho_tr, rho_pk = spearman(tr, tm), spearman(pk, tm)
print("n =", len(tr), " rho(TR) =", round(rho_tr, 3), " rho(peak) =", round(rho_pk, 3))

plt.rcParams.update({"font.size": 8, "font.family": "serif",
                     "axes.linewidth": 0.6, "xtick.major.width": 0.6,
                     "ytick.major.width": 0.6})
fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.7), sharey=True)
for ax, x, rho, lab, col in (
        (axes[0], tr, rho_tr, r"log transition-relation size ratio", "#A31A28"),
        (axes[1], pk, rho_pk, r"log peak search-BDD size ratio", "#160F99")):
    ax.axhline(0, color="0.75", lw=0.6, zorder=1)
    ax.axvline(0, color="0.75", lw=0.6, zorder=1)
    ax.scatter(x, tm, s=5, alpha=0.28, color=col, edgecolors="none", zorder=2)
    if len(x) > 1:                      # least-squares trend
        k, c0 = np.polyfit(x, tm, 1)
        xs = np.linspace(x.min(), x.max(), 50)
        ax.plot(xs, k * xs + c0, color="black", lw=1.1, zorder=3)
    ax.set_xlabel(lab)
    ax.set_title(r"$\rho = %.3f$" % rho, fontsize=9,
                 fontweight="bold" if rho > 0.4 else "normal")
    lim = np.percentile(np.abs(x), 99) * 1.1
    ax.set_xlim(-lim, lim)
axes[0].set_ylabel("log search-time ratio")
axes[0].set_ylim(np.percentile(tm, 1) * 1.2, np.percentile(tm, 99) * 1.2)
fig.tight_layout(pad=0.4)
fig.savefig(OUT, bbox_inches="tight")
print("wrote", OUT, os.path.getsize(OUT), "bytes")
