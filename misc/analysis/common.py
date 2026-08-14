"""Shared helpers for the analysis scripts. All data is read from the repo's
data/ directory, so a plain checkout reproduces every number."""
import csv
import math
import os
from collections import defaultdict

DATA = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data"))
PAPER = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "paper"))


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def merged_suite():
    """symk-selector-grand.csv with the fix7 repair rows overlaid (the dataset
    behind Table 3)."""
    grand = load("symk-selector-grand.csv")
    fix = load("symk-selector-grand-fix7.csv")
    fixed = {(r["domain"], r["problem"]) for r in fix}
    m = defaultdict(dict)
    for r in grand:
        if (r["domain"], r["problem"]) not in fixed:
            m[(r["domain"], r["problem"])][r["config"]] = r
    for r in fix:
        m[(r["domain"], r["problem"])][r["config"]] = r
    return m


def fnum(row, key):
    try:
        v = float(row[key])
        return v if v > 0 else None
    except (KeyError, TypeError, ValueError):
        return None


def solved(m, c):
    return m.get(c, {}).get("solved") == "1"


def gm(xs):
    xs = [x for x in xs if x and x > 0]
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else float("nan")


def median(xs):
    """Conventional median: average of the two middle order statistics for
    even-length samples."""
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return float("nan")
    n = len(xs)
    mid = n // 2
    return xs[mid] if n % 2 else 0.5 * (xs[mid - 1] + xs[mid])


def rankdata(xs):
    """Average ranks with proper tie handling."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(x, y):
    """Tie-aware Spearman rank correlation (Pearson on average ranks)."""
    rx, ry = rankdata(x), rankdata(y)
    mx = sum(rx) / len(rx)
    my = sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")


def sign_test(a, b):
    """Exact two-sided binomial sign test for a successes vs b failures."""
    n = a + b
    k = min(a, b)
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)
