#!/usr/bin/env python3
"""Final benchmark: stock SymK vs always-on comb-w vs the TR-probe SELECTOR.

Fairness rules baked in:
  * every config gets the SAME total wall-clock budget (translate + preprocess +
    build + search all counted), because the selector pays for its extra probe
    build and must not be given that for free;
  * wall-clock is measured by the harness itself, identically for all three;
  * plan costs are cross-checked -> any ordering that changed a cost is a bug.
"""
import argparse, concurrent.futures as cf, collections, csv, math, os, re
import signal, subprocess, sys, tempfile, shutil, time

H = os.path.expanduser("~")
FD = H + "/symk-ipc2023/fast-downward.py"
SEL = H + "/tr_select_symk.py"
LIB = H + "/lib"
SEARCH = "sym-bd(silent=true)"

def find_domain_file(ddir, prob):
    # FIXED 2026-07-24: the old version missed the prefix-domain (airport
    # p01-airport1-p1.pddl -> p01-domain.pddl) and domain_<prob> conventions,
    # then FELL BACK to the first file containing "domain" -- silently pairing
    # instances with the WRONG domain (225 fast-fails + 3 wrong-task costs in
    # symk-selector-grand.csv; see REPORT.md section 9). No promiscuous
    # fallback: unmatched -> None (skip, never mis-pair).
    stem = prob[:-len(".pddl")]
    cands = ["domain.pddl",
             stem + "-domain.pddl",
             stem.split("-")[0] + "-domain.pddl",
             "domain_" + prob,
             "domain-" + prob]
    for c in cands:
        fp = os.path.join(ddir, c)
        if os.path.isfile(fp):
            return fp
    return None

def discover(bench, listfile):
    tasks = []
    for line in open(listfile):
        parts = line.split()
        if len(parts) != 2:
            continue
        d, p = parts
        ddir = os.path.join(bench, d)
        if not os.path.isdir(ddir):
            continue
        dom = find_domain_file(ddir, p)
        prob = os.path.join(ddir, p)
        if dom and os.path.isfile(prob):
            tasks.append((d, dom, prob, p))
    return tasks

def run_one(job):
    d, dom, prob, pname, cfg, timeout, mem = job
    wd = tempfile.mkdtemp(prefix="symksel_")
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = LIB + ":" + env.get("LD_LIBRARY_PATH", "")
    if cfg == "selector":
        cmd = [sys.executable, SEL, dom, prob, "--plan-file", os.path.join(wd, "plan"),
               "--total-time-limit", str(timeout), "--memory-limit", "%sM" % mem,
               "--theta", "0.30"]
    else:
        if cfg == "on":
            env["SLBD_CONSTRAINT_ORDER"] = "1"
        cmd = [sys.executable, FD, "--overall-time-limit", "%ss" % timeout,
               "--overall-memory-limit", "%sM" % mem,
               "--plan-file", os.path.join(wd, "plan"), dom, prob,
               "--search", SEARCH]
    outp = os.path.join(wd, "out.txt")
    t0 = time.time()
    try:
        with open(outp, "w") as fo:
            p = subprocess.Popen(cmd, cwd=wd, stdout=fo, stderr=subprocess.STDOUT,
                                 env=env, start_new_session=True)
            try:
                p.wait(timeout=timeout + 180)
            except subprocess.TimeoutExpired:
                try: os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                except Exception: pass
                try: p.wait(timeout=30)
                except Exception: pass
        out = open(outp, encoding="utf-8", errors="replace").read()
    finally:
        shutil.rmtree(wd, ignore_errors=True)
    wall = time.time() - t0

    def g(pat):
        m = re.findall(pat, out)
        return m[0] if m else ""
    return (d, pname, cfg,
            1 if "Solutions found" in out else 0,
            g(r"Plan cost: (\d+)"),
            "%.3f" % wall,
            g(r"Search time: ([0-9.]+)s"),
            g(r"SELECTOR_PICKED: (\w+)"),
            g(r"SELECTOR: tr_off=(\d+)"),
            g(r"SELECTOR: tr_off=\d+ tr_on=(\d+)"),
            g(r"SELECTOR_PROBE_TIME: ([0-9.]+)"))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark-dir", required=True)
    ap.add_argument("--instance-list", required=True)
    ap.add_argument("--configs", nargs="+", default=["off", "on", "selector"])
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--memory", type=int, default=8000)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--output", required=True)
    ap.add_argument("--resume", action="store_true",
                    help="skip (instance,config) pairs already present in --output")
    a = ap.parse_args()

    tasks = discover(a.benchmark_dir, a.instance_list)
    jobs = [(d, dom, prob, pn, c, a.timeout, a.memory)
            for (d, dom, prob, pn) in tasks for c in a.configs]

    HEADER = ("domain,problem,config,solved,cost,wall,search_time,"
              "picked,tr_off,tr_on,probe_time\n")
    rows = []
    if a.resume and os.path.isfile(a.output):
        with open(a.output) as fi:
            prev = [r for r in csv.reader(fi) if len(r) == 11 and r[0] != "domain"]
        rows = [tuple(r) for r in prev]
        have = set((r[0], r[1], r[2]) for r in rows)
        before = len(jobs)
        jobs = [j for j in jobs if (j[0], j[3], j[4]) not in have]
        print("resume: %d rows already done, %d of %d runs remaining"
              % (len(rows), len(jobs), before), flush=True)
        out = open(a.output, "a")
    else:
        out = open(a.output, "w")
        out.write(HEADER)
        out.flush()
    print("%d instances x %d configs = %d runs to do, %d workers, %ds wall each"
          % (len(tasks), len(a.configs), len(jobs), a.workers, a.timeout), flush=True)
    done = 0
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for r in ex.map(run_one, jobs):
            rows.append(r)
            out.write(",".join(str(x) for x in r) + "\n")
            out.flush()
            done += 1
            if done % 25 == 0:
                print("  %d/%d done" % (done, len(jobs)), flush=True)
    out.close()

    by = collections.defaultdict(dict)
    for r in rows:
        by[(r[0], r[1])][r[2]] = tuple(str(x) for x in r)
    # rows may come from run_one (ints) or from a resumed CSV (strings)
    ok = lambda r: bool(r) and str(r[3]) == "1"
    print("\n=== coverage (of %d instances) ===" % len(by))
    for c in a.configs:
        print("  %-9s %d" % (c, sum(1 for k in by if ok(by[k].get(c)))))
    mism = sum(1 for k in by
               if len(set(by[k][c][4] for c in by[k] if ok(by[k][c]) and by[k][c][4])) > 1)
    print("cost mismatches: %d (must be 0)" % mism)

    # gain/loss ledger vs stock
    for c in a.configs:
        if c == "off":
            continue
        g = sum(1 for k in by if by[k].get(c) and by[k].get("off")
                and ok(by[k][c]) and not ok(by[k]["off"]))
        l = sum(1 for k in by if by[k].get(c) and by[k].get("off")
                and not ok(by[k][c]) and ok(by[k]["off"]))
        print("  %-9s vs stock: gained %d, lost %d (net %+d)" % (c, g, l, g - l))

    def gm(v):
        v = [x for x in v if x and x > 0]
        return math.exp(sum(math.log(x) for x in v) / len(v)) if v else None
    print("\n=== WALL-CLOCK geomean vs stock (both solved, stock wall >= 5s) ===")
    for c in a.configs:
        if c == "off":
            continue
        rr = []
        for k in by:
            o, v = by[k].get("off"), by[k].get(c)
            if ok(o) and ok(v):
                try:
                    to, tv = float(o[5]), float(v[5])
                except ValueError:
                    continue
                if to >= 5.0:
                    rr.append(tv / to)
        if rr:
            print("  %-9s %.3f (n=%d)" % (c, gm(rr), len(rr)))
    picks = collections.Counter(by[k]["selector"][7] for k in by
                                if by[k].get("selector") and by[k]["selector"][7])
    if picks:
        print("\nselector picks: %s" % dict(picks))

if __name__ == "__main__":
    main()
