#!/usr/bin/env python3
"""Parallel ordering portfolio: run several variable orderings concurrently and
return the first solution. Wall-clock = per-instance oracle over the orderings,
exploiting the causal/constraint complementarity (and the idle cores that a
single-threaded symbolic search leaves unused). Never slower than the baseline
ordering, which is one of the components.

Usage:
  portfolio.py DOMAIN PROBLEM --timeout 90 [--memory 4000] \
      --config "causal=sbd()" --config "constraint-only=sbd(...)" ...
Prints: winner config, wall-clock seconds, plan cost.
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument("problem")
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--memory", type=int, default=4000, help="per-component MB")
    ap.add_argument("--config", action="append", required=True,
                    metavar="NAME=SEARCH")
    args = ap.parse_args()

    configs = [c.split("=", 1) for c in args.config]
    procs = []
    for name, search in configs:
        wd = tempfile.mkdtemp(prefix="pf_%s_" % name)
        cmd = [sys.executable, PLANNER, "--build", "release64",
               "--overall-time-limit", "%ss" % (args.timeout + 60),
               "--search-time-limit", "%ss" % args.timeout,
               "--overall-memory-limit", "%sM" % args.memory,
               "--plan-file", os.path.join(wd, "sas_plan"),
               args.domain, args.problem, "--search", search]
        p = subprocess.Popen(cmd, cwd=wd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
        procs.append({"name": name, "p": p, "wd": wd})

    start = time.time()
    winner = None
    deadline = start + args.timeout + 120
    while time.time() < deadline:
        for d in procs:
            if d["p"].poll() is not None and "out" not in d:
                d["out"] = d["p"].stdout.read().decode("utf-8", "replace")
                if "Solution found." in d["out"]:
                    winner = d
                    break
        if winner:
            break
        if all("out" in d for d in procs):
            break  # all finished, none solved
        time.sleep(0.05)
    elapsed = time.time() - start

    # kill the losers
    for d in procs:
        if d is not winner:
            try:
                d["p"].kill()
            except OSError:
                pass

    if winner:
        cost = (re.findall(r"Plan cost: (\d+)", winner["out"]) or ["?"])[0]
        print("PORTFOLIO winner=%s wall=%.3fs cost=%s" %
              (winner["name"], elapsed, cost))
    else:
        print("PORTFOLIO winner=NONE wall=%.3fs (unsolved)" % elapsed)


if __name__ == "__main__":
    main()
