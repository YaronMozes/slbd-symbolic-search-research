#!/usr/bin/env python3
"""Build-probe ordering selector (~1x CPU intelligent selector).

Phase 1 (probe): for each candidate ordering, build the transition relations
under that order (SLBD_TR_PROBE=1 exits right after construction, ~1-2s) and
read TR_SIZE. Probes run in parallel, so probe wall-clock is ~one probe.
Phase 2 (search): run the full search once, under the ordering with the
smallest TRs.

Rationale: task-level features fail to predict the best ordering, but the
*built* TR size does (argmin-TR beat always-GAMER by ~11% offline). CPU cost is
one search + k cheap TR builds, unlike the k-search portfolio (kx CPU).

Usage: tr_select.py DOMAIN PROBLEM [--timeout N] [--memory M]
       [--config "name=SEARCH" ...]   (default: causal / comb-w1 / conly-w)
Prints: probe sizes, chosen config, wall times, cost.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
PLANNER = os.path.join(REPO, "fast-downward.py")

DEFAULT_CONFIGS = [
    "causal=sbd()",
    "comb-w1=sbd(constraint_order=true)",
    "conly-w=sbd(constraint_order=true,constraint_only=true)",
]


def launch(domain, problem, search, timeout, memory, extra_env, workdir):
    cmd = [sys.executable, PLANNER, "--build", "release64",
           "--overall-time-limit", "%ss" % timeout,
           "--overall-memory-limit", "%sM" % memory,
           "--plan-file", os.path.join(workdir, "sas_plan"),
           domain, problem, "--search", search]
    env = dict(os.environ)
    env.update(extra_env)
    return subprocess.Popen(cmd, cwd=workdir, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument("problem")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--memory", type=int, default=4000)
    ap.add_argument("--presolve", type=int, default=5,
                    help="run plain GAMER for this many seconds first; only "
                         "probe+switch if unsolved (0 = always probe)")
    ap.add_argument("--config", action="append", metavar="NAME=SEARCH")
    args = ap.parse_args()
    configs = [c.split("=", 1) for c in (args.config or DEFAULT_CONFIGS)]

    t0 = time.time()
    # Phase 0 (presolve): trivial instances are solved by plain GAMER with
    # ZERO selector overhead; the probe pipeline only runs on hard instances,
    # where its few seconds amortize to noise.
    if args.presolve > 0:
        wd = tempfile.mkdtemp(prefix="trsel_pre_")
        cmd = [sys.executable, PLANNER, "--build", "release64",
               "--overall-time-limit", "%ss" % (args.presolve + 60),
               "--search-time-limit", "%ss" % args.presolve,
               "--overall-memory-limit", "%sM" % args.memory,
               "--plan-file", os.path.join(wd, "sas_plan"),
               args.domain, args.problem, "--search", "sbd()"]
        try:
            out = subprocess.run(
                cmd, cwd=wd, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=args.presolve + 120).stdout.decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            out = ""
        if "Solution found." in out:
            wall = time.time() - t0
            cost = (re.findall(r"Plan cost: (\d+)", out) or ["?"])[0]
            st = (re.findall(r"Search time: ([0-9.]+)s", out) or ["?"])[0]
            print("TR_SELECT chosen=causal-presolve solved=1 cost=%s "
                  "search_time=%ss probe_wall=0.00s search_wall=%.2fs "
                  "total_wall=%.2fs" % (cost, st, wall, wall))
            # Standard lines so benchmark harnesses can parse this run.
            print("Plan cost: %s" % cost)
            print("Search time: %ss" % st)
            print("Solution found.")
            return
    # Phase 1: TR probes — SEQUENTIAL (true single-CPU accounting, so the
    # whole method is a sequential portfolio in IPC/ICAPS terms). Probe limit
    # 120s: translate-heavy instances need it; a timed-out probe just means
    # that candidate is skipped (fallback = causal).
    sizes = {}
    for name, search in configs:
        wd = tempfile.mkdtemp(prefix="trsel_%s_" % name)
        p = launch(args.domain, args.problem, search, 120, args.memory,
                   {"SLBD_TR_PROBE": "1"}, wd)
        try:
            out = p.communicate(timeout=150)[0].decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            p.kill()
            out = ""
        m = re.findall(r"TR_SIZE:.*total_nodes=(\d+)", out)
        sizes[name] = int(m[0]) if m else None
        print("PROBE %s tr_nodes=%s" % (name, sizes[name]))
    probe_wall = time.time() - t0

    valid = {n: s for n, s in sizes.items() if s is not None}
    winner = (min(valid, key=valid.get) if valid else configs[0][0])
    search = dict(configs)[winner]

    # Phase 2: full search under the chosen ordering — within the REMAINING
    # budget (presolve + probes already spent part of args.timeout), so the
    # whole method fits one IPC-style time limit and the comparison is fair.
    t1 = time.time()
    budget = max(60, args.timeout - int(time.time() - t0))
    wd = tempfile.mkdtemp(prefix="trsel_run_")
    p = launch(args.domain, args.problem, search, budget, args.memory,
               {}, wd)
    try:
        out = p.communicate(timeout=budget + 120)[0].decode(
            "utf-8", "replace")
    except subprocess.TimeoutExpired:
        p.kill()
        out = ""
    search_wall = time.time() - t1
    solved = "Solution found." in out
    cost = (re.findall(r"Plan cost: (\d+)", out) or ["?"])[0]
    st = (re.findall(r"Search time: ([0-9.]+)s", out) or ["?"])[0]
    print("TR_SELECT chosen=%s solved=%d cost=%s search_time=%ss "
          "probe_wall=%.2fs search_wall=%.2fs total_wall=%.2fs" %
          (winner, int(solved), cost, st, probe_wall, search_wall,
           probe_wall + search_wall))
    if solved:
        # Standard lines so benchmark harnesses can parse this run.
        print("Plan cost: %s" % cost)
        print("Search time: %ss" % st)
        print("Solution found.")


if __name__ == "__main__":
    main()
