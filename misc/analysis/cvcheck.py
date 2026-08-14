"""Reproduce the leakage claim: leave-one-domain-out vs leave-one-FAMILY-out CV
for predicting the ordering's runtime effect from preprocessing features."""
import csv, math, os, re, hashlib
from collections import defaultdict
import numpy as np

D = r"C:\Users\Yaron\Desktop\slbd-server-backup\results-2026-07-24"
rows = list(csv.DictReader(open(os.path.join(D, "co-grand-instr.csv"),
                                encoding="utf-8", errors="replace")))
inst = defaultdict(dict)
for r in rows:
    inst[(r["domain"], r["problem"])][r["config"]] = r

def fv(r, k):
    try:
        v = float(r[k]); return v if v > 0 else None
    except Exception:
        return None

X, y, dom = [], [], []
for (d, p), m in inst.items():
    c, w = m.get("causal"), m.get("comb-w")
    if not c or not w or c["solved"] != "1" or w["solved"] != "1":
        continue
    sv, ce, mn = fv(w, "sas_vars"), fv(w, "co_edges"), fv(w, "mutex_nodes")
    trc, trw = fv(c, "tr_nodes"), fv(w, "tr_nodes")
    sa, sb = fv(c, "search_time"), fv(w, "search_time")
    if None in (sv, trc, trw, sa, sb):
        continue
    ce = ce or 0.0; mn = mn or 0.0
    X.append([math.log(sv), math.log(ce + 1), math.log(mn + 1),
              math.log(trc), math.log(trw / trc), (ce or 0) / sv])
    y.append(math.log(sb / sa)); dom.append(d)
X = np.array(X); y = np.array(y); dom = np.array(dom)
print("dataset: n=%d, %d features, %d domains" % (len(y), X.shape[1], len(set(dom))))

def family(d):
    # strip IPC-year/track suffixes: woodworking-opt08-strips -> woodworking
    return re.sub(r"-(opt|sat|mco|agl)\d*.*$", "", re.sub(r"-strips$|-adl$", "", d))

fam = np.array([family(d) for d in dom])
print("families after stripping IPC-year suffixes: %d" % len(set(fam)))

def ridge_cv(groups, lam=1.0):
    preds = np.zeros_like(y)
    for g in set(groups):
        te = groups == g; tr = ~te
        if tr.sum() < 20 or te.sum() == 0:
            preds[te] = y[tr].mean() if tr.sum() else 0.0
            continue
        Xt = X[tr]; mu = Xt.mean(0); sd = Xt.std(0); sd[sd == 0] = 1
        Z = (Xt - mu) / sd
        Z = np.hstack([Z, np.ones((len(Z), 1))])
        A = Z.T @ Z + lam * np.eye(Z.shape[1]); A[-1, -1] -= lam
        beta = np.linalg.solve(A, Z.T @ y[tr])
        Ze = np.hstack([(X[te] - mu) / sd, np.ones((te.sum(), 1))])
        preds[te] = Ze @ beta
    ss_res = ((y - preds) ** 2).sum(); ss_tot = ((y - y.mean()) ** 2).sum()
    return 1 - ss_res / ss_tot

print("\nR^2 predicting log runtime ratio from preprocessing features:")
print("  leave-one-DOMAIN-out : %+.3f" % ridge_cv(dom))
print("  leave-one-FAMILY-out : %+.3f" % ridge_cv(fam))
print("\n(negative = worse than predicting the global mean)")
