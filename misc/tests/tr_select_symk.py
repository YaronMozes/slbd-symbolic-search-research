#!/usr/bin/env python3
"""TR-probe ordering selector on top of SymK (IPC-2023 planner3).

Design (settled by simulation on the 1847-instance grand A/B):
  * translate+preprocess ONCE, then reuse output.sas for both probes and the
    final search (h2 mutex groups live in that file, so they must not be redone)
  * price BOTH orderings at build time via SLBD_TR_PROBE (build TRs, print size,
    exit before search)
  * switch to constraint-aware ordering ONLY if its total TR is >= THETA smaller
    (default 0.30). Below that margin, switching has never won an instance in our
    data but has lost several, so the conservative rule is the point.
  * SHORT presolve (10s) with stock first. Measured on the grand A/B: it costs
    nothing (coverage 1000, still zero losses) yet skips probing entirely on 65%
    of the instances stock ever solves -- which is where probe overhead hurts
    most in relative terms. Longer presolve DOES cost coverage (T=30 -> 2 losses,
    T=120 -> net zero), because budget burned on a failing presolve is gone.
  * the presolve prints the stock TR size before it starts searching, so it
    doubles as the "off" probe -- only ONE extra build is ever paid.
Single CPU, sequential, optimality-preserving (ordering only permutes variables).

Emits SELECTOR: / SELECTOR_WALL: lines, then the final search's own output, so
existing log parsers (Plan cost / Search time / Solutions found) keep working.
"""
import argparse, os, re, signal, subprocess, sys, time

FD = os.path.expanduser("~/symk-ipc2023/fast-downward.py")
LIB = os.path.expanduser("~/lib")
SEARCH = "sym-bd(silent=true)"


def base_env(extra=None):
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = LIB + os.pathsep + env.get("LD_LIBRARY_PATH", "")
    if extra:
        env.update(extra)
    return env


def run(cmd, env, timeout, cwd, outfile):
    """Run to completion; kill the whole process tree on timeout. Returns (rc, text)."""
    with open(outfile, "w") as fo:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=fo, stderr=subprocess.STDOUT,
                             env=env, start_new_session=True)
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                pass
            try:
                p.wait(timeout=30)
            except Exception:
                pass
            rc = -9
    with open(outfile, encoding="utf-8", errors="replace") as fi:
        return rc, fi.read()


def tr_of(text):
    m = re.findall(r"TR_SIZE: num_trs=\d+ total_nodes=(\d+)", text)
    return int(m[0]) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument("problem")
    ap.add_argument("--plan-file", default="plan")
    ap.add_argument("--total-time-limit", type=float, default=1800.0)
    ap.add_argument("--memory-limit", default="8000M")
    ap.add_argument("--theta", type=float, default=0.30,
                    help="switch only if TR_on < (1-theta)*TR_off")
    ap.add_argument("--probe-time-limit", type=float, default=300.0)
    ap.add_argument("--presolve", type=float, default=10.0,
                    help="seconds of stock SymK before probing (0 disables)")
    args = ap.parse_args()

    t0 = time.time()
    cwd = os.getcwd()
    left = lambda: args.total_time_limit - (time.time() - t0)

    # ---- 1. translate + preprocess once -------------------------------------
    rc, out = run([sys.executable, FD, "--translate", "--preprocess",
                   "--translate-memory-limit", args.memory_limit,
                   "--preprocess-memory-limit", args.memory_limit,
                   args.domain, args.problem],
                  base_env(), min(600.0, max(1.0, left())), cwd, "prep.log")
    sas = os.path.join(cwd, "output.sas")
    if rc != 0 or not os.path.isfile(sas):
        print("SELECTOR: translate/preprocess failed rc=%s -- aborting" % rc)
        sys.stdout.write(out[-2000:])
        return 1

    # ---- 2. short presolve with stock: solves the easy majority outright, and
    #         its TR_SIZE line doubles as the "off" probe ----------------------
    trs = {}
    if args.presolve > 0:
        rc, out = run([sys.executable, FD,
                       "--search-time-limit", "%ds" % int(args.presolve),
                       "--search-memory-limit", args.memory_limit,
                       "--plan-file", args.plan_file,
                       "--search", sas,
                       "--search-options", "--search", SEARCH],
                      base_env(), args.presolve + 60.0, cwd, "presolve.log")
        if "Solutions found" in out:
            sys.stdout.write(out)
            print("SELECTOR: solved during %.0fs presolve -- no probe needed"
                  % args.presolve)
            print("SELECTOR_WALL: %.3f" % (time.time() - t0))
            print("SELECTOR_PICKED: presolve")
            return 0
        trs["off"] = tr_of(out)   # emitted before the search phase began

    # ---- 3. probe whatever the presolve did not already price ---------------
    probe_budget = min(args.probe_time_limit, max(1.0, left() / 3.0))
    need = [("on", {"SLBD_CONSTRAINT_ORDER": "1"})]
    if trs.get("off") is None:
        need.insert(0, ("off", {}))   # presolve died before the TRs were built
    for name, extra in need:
        e = base_env(extra)
        e["SLBD_TR_PROBE"] = "1"
        # driver options MUST precede the component, else they are forwarded to
        # the search binary, which rejects them ("unknown option").
        rc, out = run([sys.executable, FD,
                       "--search-memory-limit", args.memory_limit,
                       "--search", sas,
                       "--search-options", "--search", SEARCH],
                      e, probe_budget, cwd, "probe_%s.log" % name)
        trs[name] = tr_of(out)
    t_probe = time.time() - t0

    # ---- 3. decide ----------------------------------------------------------
    a, b = trs.get("off"), trs.get("on")
    if a and b and b < a * (1.0 - args.theta):
        picked, reason = "on", "TR shrank by %.1f%% (>= %.0f%%)" % (100.0 * (1 - b / a), 100 * args.theta)
    elif a and b:
        picked, reason = "off", "TR change %.1f%% below switch margin" % (100.0 * (b / a - 1))
    else:
        picked, reason = "off", "probe incomplete (tr_off=%s tr_on=%s) -- defaulting to stock" % (a, b)
    print("SELECTOR: tr_off=%s tr_on=%s ratio=%s theta=%.2f picked=%s (%s)"
          % (a, b, ("%.4f" % (b / a)) if a and b else "-", args.theta, picked, reason))
    print("SELECTOR_PROBE_TIME: %.3f" % t_probe)
    sys.stdout.flush()

    # ---- 4. final search with the chosen ordering ---------------------------
    extra = {"SLBD_CONSTRAINT_ORDER": "1"} if picked == "on" else {}
    remaining = max(1.0, left())
    rc, out = run([sys.executable, FD,
                   "--search-time-limit", "%ds" % int(remaining),
                   "--search-memory-limit", args.memory_limit,
                   "--plan-file", args.plan_file,
                   "--search", sas,
                   "--search-options", "--search", SEARCH],
                  base_env(extra), remaining + 60.0, cwd, "final.log")
    sys.stdout.write(out)
    print("SELECTOR_WALL: %.3f" % (time.time() - t0))
    print("SELECTOR_PICKED: %s" % picked)
    return 0 if "Solutions found" in out else 1


if __name__ == "__main__":
    sys.exit(main())
