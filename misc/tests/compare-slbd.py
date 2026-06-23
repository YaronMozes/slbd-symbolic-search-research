#! /usr/bin/env python3

import argparse
import csv
import os
import re
import subprocess
import sys
import time


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_BENCHMARK_DIR = os.path.join(REPO_ROOT, "misc", "tests", "benchmarks")

CONFIGS = [
    ("sbd", "sbd()"),
    ("slbd", "slbd(lm_factory=lm_rhw())"),
    ("slbd-no-guidance", "slbd(lm_guidance=false,lm_factory=lm_rhw())"),
]

GENERATED_FILES = ["output", "output.sas", "sas_plan"]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Compare symbolic bidirectional search with SLBD variants.")
    parser.add_argument(
        "--planner", default=os.path.join(REPO_ROOT, "fast-downward.py"),
        help="path to fast-downward.py")
    parser.add_argument(
        "--build", default="release64",
        help="Fast Downward build name or build bin directory")
    parser.add_argument(
        "--timeout", type=int, default=300,
        help="per-run planner search timeout in seconds")
    parser.add_argument(
        "--repeat", type=int, default=1,
        help="number of repetitions per task/config")
    parser.add_argument(
        "--output", default=os.path.join(REPO_ROOT, "slbd-comparison.csv"),
        help="CSV summary path")
    parser.add_argument(
        "--log-dir", default=os.path.join(REPO_ROOT, "slbd-comparison-logs"),
        help="directory for raw planner logs and plans")
    parser.add_argument(
        "--task", nargs=2, action="append", metavar=("DOMAIN", "PROBLEM"),
        help="task to run; defaults to all bundled misc/tests/benchmarks tasks")
    return parser.parse_args()


def default_tasks():
    tasks = []
    for domain_name in sorted(os.listdir(DEFAULT_BENCHMARK_DIR)):
        domain_dir = os.path.join(DEFAULT_BENCHMARK_DIR, domain_name)
        domain_file = os.path.join(domain_dir, "domain.pddl")
        if not os.path.isfile(domain_file):
            continue
        for filename in sorted(os.listdir(domain_dir)):
            if filename == "domain.pddl" or not filename.endswith(".pddl"):
                continue
            tasks.append((domain_file, os.path.join(domain_dir, filename)))
    return tasks


def remove_generated_files():
    for filename in GENERATED_FILES:
        path = os.path.join(REPO_ROOT, filename)
        if os.path.exists(path):
            os.remove(path)


def task_name(problem):
    rel_path = os.path.relpath(problem, DEFAULT_BENCHMARK_DIR)
    return rel_path.replace(os.sep, ":")


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")


def parse_output(stdout, returncode, elapsed):
    result = {
        "returncode": returncode,
        "wall_time": "%.3f" % elapsed,
        "plan_cost": "",
        "plan_length": "",
        "actual_search_time": "",
        "peak_memory_kb": "",
        "fw_time": "",
        "bw_time": "",
        "fw_steps": "",
        "bw_steps": "",
        "landmarks_total": "",
        "landmarks_simple": "",
        "landmarks_disjunctive": "",
        "landmarks_conjunctive": "",
        "landmarks_min_cost_sum": "",
        "guidance_forward": "",
        "guidance_backward": "",
        "fallback_to_bdd_nodes": "",
        "guidance_total": "",
        "coverage_forward_unweighted": "",
        "coverage_forward_weighted": "",
        "coverage_backward_unweighted": "",
        "coverage_backward_weighted": "",
    }

    patterns = [
        ("plan_cost", r"Plan cost: (\d+)"),
        ("plan_length", r"Plan length: (\d+) step"),
        ("actual_search_time", r"Actual search time: ([0-9.]+)s"),
    ]
    for key, pattern in patterns:
        match = re.search(pattern, stdout)
        if match:
            result[key] = match.group(1)

    memory_matches = re.findall(r"([0-9]+) KB", stdout)
    if memory_matches:
        result["peak_memory_kb"] = memory_matches[-1]

    for direction in ["fw", "bw"]:
        match = re.search(
            r"Exp %s time: ([0-9.]+)s .*? in ([0-9]+) steps" % direction,
            stdout)
        if match:
            result["%s_time" % direction] = match.group(1)
            result["%s_steps" % direction] = match.group(2)

    match = re.search(
        r"Landmark guidance index: ([0-9]+) landmarks "
        r"\(simple: ([0-9]+), disjunctive: ([0-9]+), "
        r"conjunctive: ([0-9]+), min-cost sum: ([0-9]+)\)",
        stdout)
    if match:
        keys = [
            "landmarks_total", "landmarks_simple", "landmarks_disjunctive",
            "landmarks_conjunctive", "landmarks_min_cost_sum",
        ]
        result.update(dict(zip(keys, match.groups())))

    match = re.search(
        r"Landmark guidance decisions: forward=([0-9]+), "
        r"backward=([0-9]+), fallback_to_bdd_nodes=([0-9]+), total=([0-9]+)",
        stdout)
    if match:
        keys = [
            "guidance_forward", "guidance_backward",
            "fallback_to_bdd_nodes", "guidance_total",
        ]
        result.update(dict(zip(keys, match.groups())))

    for direction in ["forward", "backward"]:
        match = re.search(
            r"Landmark coverage %s: unweighted=([0-9]+/[0-9]+), "
            r"weighted=([0-9]+/[0-9]+)" % direction,
            stdout)
        if match:
            result["coverage_%s_unweighted" % direction] = match.group(1)
            result["coverage_%s_weighted" % direction] = match.group(2)

    return result


def run_config(args, domain, problem, config_name, search, repetition):
    task = task_name(problem)
    stem = safe_name("%s-%s-r%s" % (task, config_name, repetition))
    log_path = os.path.join(args.log_dir, stem + ".log")
    plan_path = os.path.join(args.log_dir, stem + ".plan")

    remove_generated_files()
    cmd = [
        sys.executable,
        args.planner,
        "--build", args.build,
        "--overall-time-limit", "%ss" % (args.timeout + 60),
        "--search-time-limit", "%ss" % args.timeout,
        "--plan-file", plan_path,
        domain,
        problem,
        "--search", search,
    ]

    start_time = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            timeout=args.timeout + 120)
        stdout = proc.stdout
        returncode = proc.returncode
    except subprocess.TimeoutExpired as err:
        stdout = err.stdout or ""
        stdout += "\nBenchmark harness timeout after %s seconds.\n" % (args.timeout + 120)
        returncode = -1
    elapsed = time.time() - start_time

    with open(log_path, "w", encoding="utf-8") as log_file:
        log_file.write("$ %s\n\n" % " ".join(cmd))
        log_file.write(stdout)

    row = parse_output(stdout, returncode, elapsed)
    row.update({
        "task": task,
        "config": config_name,
        "search": search,
        "repeat": repetition,
        "log": os.path.relpath(log_path, REPO_ROOT),
        "plan": os.path.relpath(plan_path, REPO_ROOT),
    })
    remove_generated_files()
    return row


def main():
    args = parse_args()
    os.makedirs(args.log_dir, exist_ok=True)
    tasks = args.task or default_tasks()
    rows = []

    for domain, problem in tasks:
        for repetition in range(1, args.repeat + 1):
            for config_name, search in CONFIGS:
                print("%s %s repeat %s" % (task_name(problem), config_name, repetition))
                rows.append(run_config(args, domain, problem, config_name, search, repetition))

    fieldnames = [
        "task", "config", "repeat", "returncode", "plan_cost", "plan_length",
        "actual_search_time", "wall_time", "peak_memory_kb", "fw_time",
        "bw_time", "fw_steps", "bw_steps", "landmarks_total",
        "landmarks_simple", "landmarks_disjunctive", "landmarks_conjunctive",
        "landmarks_min_cost_sum", "coverage_forward_unweighted",
        "coverage_forward_weighted", "coverage_backward_unweighted",
        "coverage_backward_weighted", "guidance_forward", "guidance_backward",
        "fallback_to_bdd_nodes", "guidance_total", "search", "log", "plan",
    ]
    with open(args.output, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print("Wrote %s" % args.output)


if __name__ == "__main__":
    main()
