#!/usr/bin/env python3
"""Honest wall-clock bench: GAMER vs the TR-probe selector (tr_select).

Runs sequentially (no contention) and measures TOTAL wall per instance for
both arms, so the selector's probe overhead is fully counted.
"""
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
PLANNER = os.path.join(REPO, "fast-downward.py")

DOMAINS = ["blocks", "woodworking-opt08-strips", "pipesworld-notankage",
           "scanalyzer-08-strips", "tpp", "transport-opt08-strips",
           "satellite", "rovers", "depot", "gripper"]


def gamer_arm(dom, prob, timeout, memory):
    wd = tempfile.mkdtemp(prefix="selb_g_")
    t0 = time.time()
    cmd = [sys.executable, PLANNER, "--build", "release64",
           "--overall-time-limit", "%ss" % timeout,
           "--overall-memory-limit", "%sM" % memory,
           "--plan-file", os.path.join(wd, "sas_plan"),
           dom, prob, "--search", "sbd()"]
    try:
        out = subprocess.run(cmd, cwd=wd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, timeout=timeout + 120
                             ).stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        out = ""
    wall = time.time() - t0
    return ("Solution found." in out), wall


def selector_arm(dom, prob, timeout, memory):
    cmd = [sys.executable, os.path.join(HERE, "tr_select.py"), dom, prob,
           "--timeout", str(timeout), "--memory", str(memory)]
    try:
        out = subprocess.run(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, timeout=timeout + 240
                             ).stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        out = ""
    m = re.search(r"TR_SELECT chosen=(\S+) solved=(\d) .*total_wall=([0-9.]+)",
                  out)
    if not m:
        return None, False, float("nan")
    return m.group(1), m.group(2) == "1", float(m.group(3))


def main():
    bench = sys.argv[1]
    timeout, memory, n = 90, 4000, 8
    out = open(os.path.join(REPO, "slbd-results", "selector-bench.csv"), "w")
    out.write("domain,problem,gamer_solved,gamer_wall,sel_chosen,sel_solved,"
              "sel_wall\n")
    for d in DOMAINS:
        dom = os.path.join(bench, d, "domain.pddl")
        probs = sorted(f for f in os.listdir(os.path.join(bench, d))
                       if f.endswith(".pddl") and f != "domain.pddl")[:n]
        for p in probs:
            prob = os.path.join(bench, d, p)
            gs, gw = gamer_arm(dom, prob, timeout, memory)
            ch, ss, sw = selector_arm(dom, prob, timeout, memory)
            out.write("%s,%s,%d,%.2f,%s,%d,%.2f\n" %
                      (d, p, gs, gw, ch, ss, sw))
            out.flush()
            print("%s/%s gamer=%d/%.1fs  sel[%s]=%d/%.1fs" %
                  (d, p, gs, gw, ch, ss, sw), flush=True)
    out.close()


if __name__ == "__main__":
    main()
