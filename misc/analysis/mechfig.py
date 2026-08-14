"""Generates paper/mechanism.pdf (Figure 2) from data/co-grand-instr.csv.
Spearman correlations use tie-aware average ranks."""
import math
import os
from collections import defaultdict

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from common import DATA, PAPER, load, fnum, spearman

rows = load("co-grand-instr.csv")
inst = defaultdict(dict)
for r in rows:
    inst[(r["domain"], r["problem"])][r["config"]] = r

tr, pk, tm = [], [], []
for m in inst.values():
    c, w = m.get("causal"), m.get("comb-w")
    if not c or not w or c["solved"] != "1" or w["solved"] != "1":
        continue
    try:
        if float(w.get("co_edges") or 0) <= 0:      # method structurally inactive
            continue
    except ValueError:
        continue
    a, b = fnum(c, "tr_nodes"), fnum(w, "tr_nodes")
    pa, pb = fnum(c, "peak_bdd_nodes"), fnum(w, "peak_bdd_nodes")
    sa, sb = fnum(c, "search_time"), fnum(w, "search_time")
    if None in (a, b, pa, pb, sa, sb):
        continue
    tr.append(math.log(b / a))
    pk.append(math.log(pb / pa))
    tm.append(math.log(sb / sa))

rho_tr = spearman(tr, tm)
rho_pk = spearman(pk, tm)
print("n = %d   rho(TR) = %.3f   rho(peak) = %.3f" % (len(tr), rho_tr, rho_pk))

tr, pk, tm = np.array(tr), np.array(pk), np.array(tm)
plt.rcParams.update({"font.size": 8, "font.family": "serif",
                     "axes.linewidth": 0.6, "xtick.major.width": 0.6,
                     "ytick.major.width": 0.6})
fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.7), sharey=True)
for ax, x, rho, lab, col in (
        (axes[0], tr, rho_tr, "log transition-relation size ratio", "#A31A28"),
        (axes[1], pk, rho_pk, "log peak search-BDD size ratio", "#160F99")):
    ax.axhline(0, color="0.75", lw=0.6, zorder=1)
    ax.axvline(0, color="0.75", lw=0.6, zorder=1)
    ax.scatter(x, tm, s=5, alpha=0.28, color=col, edgecolors="none", zorder=2)
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
out = os.path.join(PAPER, "mechanism.pdf")
fig.savefig(out, bbox_inches="tight")
print("wrote", out)
