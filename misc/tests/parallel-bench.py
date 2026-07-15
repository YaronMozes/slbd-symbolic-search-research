#!/usr/bin/env python3
"""Parallel benchmark runner for symbolic-search ordering experiments.

Runs each (task, config) in its own temp dir (so output.sas doesn't collide)
with a per-run memory + time limit, across a thread pool. Reports solved, plan
cost, search time, and mutex-BDD size per run, plus a per-domain ratio summary.

Coverage/cost are deterministic; timing under parallelism has some contention
noise, but per-domain RATIOS (config vs the first config) are largely preserved.
Re-validate headline timing numbers sequentially (--workers 1) on key domains.
"""
import argparse
import concurrent.futures as cf
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
PLANNER = os.path.join(REPO, "fast-downward.py")


def find_domain_file(ddir, prob):
    # FD conventions: shared domain.pddl; per-instance <stem>-domain.pddl
    # (openstacks/parcprinter: p01-domain.pddl for p01.pddl) or
    # <prefix>-domain.pddl (airport: p01-domain.pddl for p01-airport1-p1.pddl);
    # or domain_<prob>.
    stem = prob[:-len(".pddl")]
    cands = [os.path.join(ddir, "domain.pddl"),
             os.path.join(ddir, stem + "-domain.pddl"),
             os.path.join(ddir, stem.split("-")[0] + "-domain.pddl"),
             os.path.join(ddir, "domain_" + prob),
             os.path.join(ddir, "domain-" + prob)]
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def discover(bench, domains, n):
    tasks = []
    for d in domains:
        ddir = os.path.join(bench, d)
        if not os.path.isdir(ddir):
            continue
        probs = sorted(f for f in os.listdir(ddir)
                       if f.endswith(".pddl") and "domain" not in f)
        count = 0
        for p in probs:
            if count >= n:
                break
            dom = find_domain_file(ddir, p)
            if dom is None:
                continue
            tasks.append((d, dom, os.path.join(ddir, p), p))
            count += 1
    return tasks


def run_one(job):
    d, dom, prob, pname, cfgname, search, timeout, mem, env_over = job
    workdir = tempfile.mkdtemp(prefix="pbench_")
    plan = os.path.join(workdir, "sas_plan")
    if search == "SELECTOR":
        # Run the TR-probe selector (a sequential portfolio: presolve ->
        # sequential probes -> search, all within ONE `timeout` budget).
        cmd = [sys.executable, os.path.join(HERE, "tr_select.py"), dom, prob,
               "--timeout", str(timeout), "--memory", str(mem)]
        subprocess_cap = timeout + 300
    elif search == "SYMK":
        # External SoTA baseline: SymK (IPC-2023 planner3), blind symbolic
        # bidirectional search (sym-bd) -- the algorithmic analog of our sbd().
        # Same time+memory budget as our runs for a fair comparison; SymK's own
        # default build. Its success line is "Solutions found." (plural).
        symk = os.path.expanduser("~/symk-ipc2023/fast-downward.py")
        cmd = [sys.executable, symk,
               "--overall-time-limit", "%ss" % (timeout + 60),
               "--search-time-limit", "%ss" % timeout,
               "--overall-memory-limit", "%sM" % mem,
               "--plan-file", plan, dom, prob,
               "--search", "sym-bd(silent=true)"]
        subprocess_cap = timeout + 180
    else:
        cmd = [sys.executable, PLANNER, "--build", "release64",
               "--overall-time-limit", "%ss" % (timeout + 60),
               "--search-time-limit", "%ss" % timeout,
               "--overall-memory-limit", "%sM" % mem,
               "--plan-file", plan, dom, prob, "--search", search]
        subprocess_cap = timeout + 180
    env = dict(os.environ)
    env.update(env_over)
    try:
        out = subprocess.run(cmd, cwd=workdir, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, timeout=subprocess_cap,
                             env=env
                             ).stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        out = "HARNESS TIMEOUT"
    finally:
        for f in os.listdir(workdir):
            try:
                os.remove(os.path.join(workdir, f))
            except OSError:
                pass
        try:
            os.rmdir(workdir)
        except OSError:
            pass
    solved = 1 if ("Solution found." in out or "Solutions found." in out) else 0
    cost = (re.findall(r"Plan cost: (\d+)", out) or [""])[0]
    st = (re.findall(r"Search time: ([0-9.]+)s", out) or [""])[0]
    mx = (re.findall(r"MUTEX_BDD_SIZE:.*total_nodes=(\d+)", out) or [""])[0]
    tv = (re.findall(r"Translator variables: (\d+)", out) or [""])[0]
    ce = (re.findall(r"co_occurrence_edges_added=(\d+)", out) or [""])[0]
    tr = (re.findall(r"TR_SIZE:.*total_nodes=(\d+)", out) or [""])[0]
    # SEARCH_STATS: fields (added by SymbolicSearch::print_statistics).
    # Expanded-state counts come from BDD model counting -> can be ~1e18, keep as
    # float text (accept scientific notation); node counts are plain ints.
    fx = (re.findall(r"fw_expanded_states=([0-9.eE+]+)", out) or [""])[0]
    bx = (re.findall(r"bw_expanded_states=([0-9.eE+]+)", out) or [""])[0]
    fcn = (re.findall(r"fw_closed_nodes=(\d+)", out) or [""])[0]
    bcn = (re.findall(r"bw_closed_nodes=(\d+)", out) or [""])[0]
    pk = (re.findall(r"peak_bdd_nodes=(\d+)", out) or [""])[0]
    fbn = (re.findall(r"final_bdd_nodes=(\d+)", out) or [""])[0]
    # Per-direction image/step counts from "Exp fw time: ... in N steps (M truncated)".
    fws = (re.findall(r"Exp fw time:.*? in (\d+) steps", out) or [""])[0]
    bws = (re.findall(r"Exp bw time:.*? in (\d+) steps", out) or [""])[0]
    fwt = (re.findall(r"Exp fw time:.*?\((\d+) truncated\)", out) or [""])[0]
    bwt = (re.findall(r"Exp bw time:.*?\((\d+) truncated\)", out) or [""])[0]
    return (d, pname, cfgname, solved, cost, st, mx, tv, ce, tr,
            fx, bx, fcn, bcn, pk, fbn, fws, bws, fwt, bwt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark-dir", required=True)
    ap.add_argument("--domains", nargs="+", required=True)
    ap.add_argument("--tasks-per-domain", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--memory", type=int, default=3000, help="per-run MB")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--config", action="append", required=True,
                    metavar="NAME=SEARCH", help="e.g. causal=sbd()")
    ap.add_argument("--config-env", action="append", default=[],
                    metavar="NAME:KEY=VAL",
                    help="per-config env override, repeatable")
    ap.add_argument("--output", required=True)
    ap.add_argument("--resume", action="store_true",
                    help="skip (domain,problem,config) rows already in output")
    args = ap.parse_args()

    configs = [c.split("=", 1) for c in args.config]
    cfg_env = {}
    for spec in args.config_env:
        name, kv = spec.split(":", 1)
        k, v = kv.split("=", 1)
        cfg_env.setdefault(name, {})[k] = v
    tasks = discover(args.benchmark_dir, args.domains, args.tasks_per_domain)
    jobs = [(d, dom, prob, pn, cn, cs, args.timeout, args.memory,
             cfg_env.get(cn, {}))
            for (d, dom, prob, pn) in tasks for (cn, cs) in configs]

    already = set()
    if args.resume and os.path.isfile(args.output):
        import csv as _csv
        with open(args.output) as f:
            for r in _csv.DictReader(f):
                already.add((r["domain"], r["problem"], r["config"]))
        jobs = [j for j in jobs if (j[0], j[3], j[4]) not in already]
    print("%d tasks x %d configs = %d runs (%d skipped via resume), "
          "%d workers, %dMB/run" % (
              len(tasks), len(configs), len(jobs), len(already),
              args.workers, args.memory), flush=True)

    # Incremental, crash-safe CSV: append + flush each finished run.
    new_file = not (args.resume and os.path.isfile(args.output))
    out = open(args.output, "w" if new_file else "a")
    if new_file:
        out.write("domain,problem,config,solved,cost,search_time,mutex_nodes,"
                  "sas_vars,co_edges,tr_nodes,"
                  "fw_expanded,bw_expanded,fw_closed_nodes,bw_closed_nodes,"
                  "peak_bdd_nodes,final_bdd_nodes,"
                  "fw_steps,bw_steps,fw_truncated,bw_truncated\n")
        out.flush()
    rows = []
    done = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(run_one, jobs):
            rows.append(r)
            out.write(",".join(str(x) for x in r) + "\n")
            out.flush()
            done += 1
            if done % 20 == 0:
                print("  %d/%d done" % (done, len(jobs)), flush=True)
    out.close()
    print("Wrote %s" % args.output)

    # quick summary: per-domain solved + geomean ratio vs first config
    import math
    cfgnames = [c[0] for c in configs]
    base = cfgnames[0]
    by = {}
    for row in rows:
        # width-robust: summary only needs domain, problem, config, solved, search_time
        d, pn, cn, s, st = row[0], row[1], row[2], row[3], row[5]
        by.setdefault((d, pn), {})[cn] = (s, st)
    print("\nPer-domain: solved counts and geomean search-time ratio vs %s" % base)
    domains = sorted(set(d for (d, _) in by))
    for d in domains:
        line = "  %-22s" % d
        for cn in cfgnames:
            solved = sum(1 for (dd, pn), m in by.items()
                         if dd == d and m.get(cn, (0, ""))[0] == 1)
            line += " %s=%d" % (cn, solved)
        # geomean ratio on commonly solved (all configs solved)
        for cn in cfgnames[1:]:
            ratios = []
            for (dd, pn), m in by.items():
                if dd != d:
                    continue
                if all(m.get(c, (0, ""))[0] == 1 and m[c][1] for c in cfgnames):
                    b = float(m[base][1])
                    v = float(m[cn][1])
                    if b > 0:
                        ratios.append(v / b)
            if ratios:
                g = math.exp(sum(math.log(x) for x in ratios) / len(ratios))
                line += "  %s/%s=%.2f" % (cn, base, g)
        print(line)


if __name__ == "__main__":
    main()
