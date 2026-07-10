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


def discover(bench, domains, n):
    tasks = []
    for d in domains:
        dom = os.path.join(bench, d, "domain.pddl")
        if not os.path.isfile(dom):
            continue
        probs = sorted(f for f in os.listdir(os.path.join(bench, d))
                       if f.endswith(".pddl") and f != "domain.pddl")
        for p in probs[:n]:
            tasks.append((d, dom, os.path.join(bench, d, p), p))
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
    solved = 1 if "Solution found." in out else 0
    cost = (re.findall(r"Plan cost: (\d+)", out) or [""])[0]
    st = (re.findall(r"Search time: ([0-9.]+)s", out) or [""])[0]
    mx = (re.findall(r"MUTEX_BDD_SIZE:.*total_nodes=(\d+)", out) or [""])[0]
    tv = (re.findall(r"Translator variables: (\d+)", out) or [""])[0]
    ce = (re.findall(r"co_occurrence_edges_added=(\d+)", out) or [""])[0]
    tr = (re.findall(r"TR_SIZE:.*total_nodes=(\d+)", out) or [""])[0]
    return (d, pname, cfgname, solved, cost, st, mx, tv, ce, tr)


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
    print("%d tasks x %d configs = %d runs, %d workers, %dMB/run" % (
        len(tasks), len(configs), len(jobs), args.workers, args.memory))

    rows = []
    done = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(run_one, jobs):
            rows.append(r)
            done += 1
            if done % 20 == 0:
                print("  %d/%d done" % (done, len(jobs)), flush=True)

    with open(args.output, "w") as f:
        f.write("domain,problem,config,solved,cost,search_time,mutex_nodes,"
                "sas_vars,co_edges,tr_nodes\n")
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")
    print("Wrote %s" % args.output)

    # quick summary: per-domain solved + geomean ratio vs first config
    import math
    cfgnames = [c[0] for c in configs]
    base = cfgnames[0]
    by = {}
    for (d, pn, cn, s, cost, st, mx, tv, ce, tr) in rows:
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
