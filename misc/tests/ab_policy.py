#!/usr/bin/env python3
"""Pre-registered per-domain ordering-policy A/B (see PREREG-ab-policy.md).

KNOWN LIMITATIONS of this driver, as released (noted after an external audit;
the published data/ab-policy.csv was produced by exactly this version):
  * The queue always emits `stock` before `policy` for a given instance. This
    keeps the two arms adjacent under similar load but does NOT counterbalance
    order; a rerun should alternate which arm goes first across replicates.
  * The child environment is inherited, so SLBD_CO_NORM / SLBD_CO_SKIP_CAUSAL /
    SLBD_CO_ONLY are only guaranteed unset if the launching shell is clean.
    They were unset for the released runs; a rerun should clear them explicitly.


stock vs policy (constraint ordering, per-domain weight), 2 replicates,
stock/policy for the same instance ADJACENT in the queue (contention-symmetric).
Crash-safe: --resume skips completed (domain,problem,config,rep) rows.

This file defines THE final method's policy: SIGNAL_DOMAINS maps each signal
domain to its ordering weight; on every other domain the policy is stock SymK
by construction. Result (gates in PREREG-ab-policy.md, all passed): +6 coverage
in both replicates, woodworking wall geomean 0.647.
"""
import argparse
import csv
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import concurrent.futures as cf

H = os.path.expanduser("~")
FD = H + "/symk-ipc2023/fast-downward.py"
LIB = H + "/lib"
SEARCH = "sym-bd(silent=true)"

SIGNAL_DOMAINS = {
    "woodworking-opt08-strips": "2.0",
    "woodworking-opt11-strips": "2.0",
    "parking-opt14-strips": "1.0",
    "barman-opt11-strips": "1.0",
    "tpp": "1.0",
}


def find_domain_file(ddir, prob):
    # All 5 signal domains use a shared domain.pddl; keep the full proven
    # candidate list anyway (defense in depth, no promiscuous fallback).
    stem = prob[:-len(".pddl")]
    for c in ["domain.pddl", stem + "-domain.pddl",
              stem.split("-")[0] + "-domain.pddl",
              "domain_" + prob, "domain-" + prob]:
        fp = os.path.join(ddir, c)
        if os.path.isfile(fp):
            return fp
    return None


def run_one(job):
    d, dom, prob, pname, cfg, rep, timeout, mem = job
    wd = tempfile.mkdtemp(prefix="abpol_")
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = LIB + ":" + env.get("LD_LIBRARY_PATH", "")
    if cfg == "policy":
        env["SLBD_CONSTRAINT_ORDER"] = "1"
        env["SLBD_CO_WEIGHT"] = SIGNAL_DOMAINS[d]
    cmd = [sys.executable, FD, "--overall-time-limit", "%ss" % timeout,
           "--overall-memory-limit", "%sM" % mem,
           "--plan-file", os.path.join(wd, "plan"), dom, prob,
           "--search", SEARCH]
    outp = os.path.join(wd, "out.txt")
    t0 = time.time()
    try:
        with open(outp, "w") as fo:
            p = subprocess.Popen(cmd, cwd=wd, stdout=fo,
                                 stderr=subprocess.STDOUT,
                                 env=env, start_new_session=True)
            try:
                p.wait(timeout=timeout + 180)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                except Exception:
                    pass
                try:
                    p.wait(timeout=30)
                except Exception:
                    pass
        out = open(outp, encoding="utf-8", errors="replace").read()
    finally:
        shutil.rmtree(wd, ignore_errors=True)
    wall = time.time() - t0

    def g(pat):
        m = re.findall(pat, out)
        return m[0] if m else ""
    return (d, pname, cfg, rep,
            1 if "Solutions found" in out else 0,
            g(r"Plan cost: (\d+)"),
            "%.3f" % wall,
            g(r"Search time: ([0-9.]+)s"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark-dir", required=True)
    ap.add_argument("--instance-list", required=True)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--memory", type=int, default=8000)
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--output", required=True)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    tasks = []
    for line in open(args.instance_list):
        parts = line.split()
        if len(parts) != 2 or parts[0] not in SIGNAL_DOMAINS:
            continue
        d, p = parts
        ddir = os.path.join(args.benchmark_dir, d)
        dom = find_domain_file(ddir, p)
        prob = os.path.join(ddir, p)
        if dom and os.path.isfile(prob):
            tasks.append((d, dom, prob, p))

    # Interleave: per replicate, per instance, stock then policy ADJACENT.
    jobs = []
    for rep in (1, 2):
        for (d, dom, prob, p) in tasks:
            for cfg in ("stock", "policy"):
                jobs.append((d, dom, prob, p, cfg, rep,
                             args.timeout, args.memory))

    done = set()
    if args.resume and os.path.isfile(args.output):
        with open(args.output) as f:
            for r in csv.reader(f):
                if len(r) >= 4 and r[0] != "domain":
                    done.add((r[0], r[1], r[2], r[3]))
        jobs = [j for j in jobs
                if (j[0], j[3], j[4], str(j[5])) not in done]
    print("%d instances -> %d runs to do (%d skipped), %d workers"
          % (len(tasks), len(jobs), len(done), args.workers), flush=True)

    new = not (args.resume and os.path.isfile(args.output))
    out = open(args.output, "w" if new else "a")
    if new:
        out.write("domain,problem,config,rep,solved,cost,wall,search_time\n")
        out.flush()
    n = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(run_one, jobs):
            out.write(",".join(str(x) for x in r) + "\n")
            out.flush()
            n += 1
            if n % 10 == 0:
                print("  %d/%d done" % (n, len(jobs)), flush=True)
    out.close()
    print("AB_POLICY_DONE")


if __name__ == "__main__":
    main()
