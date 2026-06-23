#! /usr/bin/env python3

import argparse
import csv
import math
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_BENCHMARK_DIR = os.path.join(REPO_ROOT, "misc", "tests", "benchmarks")
DEFAULT_RESULTS_DIR = os.path.join(REPO_ROOT, "slbd-results")

CONFIG_PRESETS = {
    "smoke": [
        ("sbd", "sbd()"),
        ("slbd", "slbd(lm_factory=lm_rhw())"),
        ("slbd-no-guidance", "slbd(lm_guidance=false,lm_factory=lm_rhw())"),
    ],
    "tuning": [
        ("slbd", "slbd(lm_factory=lm_rhw())"),
        ("slbd-weighted", "slbd(lm_factory=lm_rhw(),lm_guidance_score=weighted)"),
        ("slbd-slack0", "slbd(lm_factory=lm_rhw(),lm_node_slack_percent=0)"),
        ("slbd-slack25", "slbd(lm_factory=lm_rhw(),lm_node_slack_percent=25)"),
        ("slbd-slack50", "slbd(lm_factory=lm_rhw(),lm_node_slack_percent=50)"),
        ("slbd-eval5", "slbd(lm_factory=lm_rhw(),lm_eval_frequency=5)"),
    ],
}

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
        "--benchmark-dir", default=DEFAULT_BENCHMARK_DIR,
        help="benchmark root with domain subdirectories")
    parser.add_argument(
        "--include-domain", action="append", default=[],
        help="domain directory name to include; may be used multiple times")
    parser.add_argument(
        "--tasks-per-domain", type=int,
        help="deterministically keep at most this many sorted tasks per domain")
    parser.add_argument(
        "--config-preset", choices=["smoke", "tuning", "confirm"], default="smoke",
        help="configuration set to run")
    parser.add_argument(
        "--confirm-candidate",
        default="slbd(lm_factory=lm_rhw(),lm_guidance_score=weighted)",
        help="candidate search config used by --config-preset confirm")
    parser.add_argument(
        "--confirm-candidate-name", default="slbd-confirm-candidate",
        help="CSV config name for --confirm-candidate")
    parser.add_argument(
        "--timeout", type=int, default=300,
        help="per-run planner search timeout in seconds")
    parser.add_argument(
        "--repeat", type=int, default=1,
        help="number of repetitions per task/config")
    parser.add_argument(
        "--output",
        default=os.path.join(DEFAULT_RESULTS_DIR, "slbd-comparison.csv"),
        help="CSV summary path")
    parser.add_argument(
        "--summary",
        default=os.path.join(DEFAULT_RESULTS_DIR, "summary.txt"),
        help="text summary path")
    parser.add_argument(
        "--log-dir", default=os.path.join(DEFAULT_RESULTS_DIR, "logs"),
        help="directory for raw planner logs and plans")
    parser.add_argument(
        "--task", nargs=2, action="append", metavar=("DOMAIN", "PROBLEM"),
        help="task to run; defaults to tasks discovered under --benchmark-dir")
    return parser.parse_args()


def get_configs(args):
    if args.config_preset == "confirm":
        return [
            ("sbd", "sbd()"),
            ("slbd-no-guidance", "slbd(lm_guidance=false,lm_factory=lm_rhw())"),
            ("slbd", "slbd(lm_factory=lm_rhw())"),
            (args.confirm_candidate_name, args.confirm_candidate),
        ]
    return CONFIG_PRESETS[args.config_preset]


def discover_tasks(benchmark_dir, include_domains, tasks_per_domain):
    tasks = []
    include_domains = set(include_domains)
    for domain_name in sorted(os.listdir(benchmark_dir)):
        if include_domains and domain_name not in include_domains:
            continue
        domain_dir = os.path.join(benchmark_dir, domain_name)
        domain_file = os.path.join(domain_dir, "domain.pddl")
        if not os.path.isdir(domain_dir) or not os.path.isfile(domain_file):
            continue
        problems = [
            os.path.join(domain_dir, filename)
            for filename in sorted(os.listdir(domain_dir))
            if filename != "domain.pddl" and filename.endswith(".pddl")
        ]
        if tasks_per_domain is not None:
            problems = problems[:max(0, tasks_per_domain)]
        tasks.extend((domain_file, problem) for problem in problems)
    return tasks


def remove_generated_files():
    for filename in GENERATED_FILES:
        path = os.path.join(REPO_ROOT, filename)
        if os.path.exists(path):
            os.remove(path)


def task_name(problem, benchmark_dir):
    try:
        rel_path = os.path.relpath(problem, benchmark_dir)
    except ValueError:
        rel_path = os.path.abspath(problem)
    if rel_path.startswith(".."):
        rel_path = os.path.relpath(problem, REPO_ROOT)
    return rel_path.replace(os.sep, ":")


def task_domain(task):
    return task.split(":", 1)[0]


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
        "lm_guidance_score": "",
        "guidance_forward": "",
        "guidance_backward": "",
        "fallback_to_bdd_nodes": "",
        "guidance_total": "",
        "guidance_evaluated": "",
        "coverage_recomputations": "",
        "fallback_node_slack": "",
        "fallback_equal_score": "",
        "fallback_disabled_no_landmarks": "",
        "fallback_non_searchable": "",
        "coverage_forward_unweighted": "",
        "coverage_forward_weighted": "",
        "coverage_backward_unweighted": "",
        "coverage_backward_weighted": "",
    }

    patterns = [
        ("plan_cost", r"Plan cost: (\d+)"),
        ("plan_length", r"Plan length: (\d+) step"),
        ("actual_search_time", r"Actual search time: ([0-9.eE+-]+)s"),
        ("lm_guidance_score", r"Landmark guidance score: ([A-Za-z0-9_-]+)"),
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
            r"Exp %s time: ([0-9.eE+-]+)s .*? in ([0-9]+) steps" % direction,
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

    match = re.search(
        r"Landmark guidance evaluations: guidance_evaluated=([0-9]+), "
        r"coverage_recomputations=([0-9]+)",
        stdout)
    if match:
        result["guidance_evaluated"], result["coverage_recomputations"] = match.groups()

    match = re.search(
        r"Landmark guidance fallback reasons: node_slack=([0-9]+), "
        r"equal_score=([0-9]+), disabled_no_landmarks=([0-9]+), "
        r"non_searchable=([0-9]+)",
        stdout)
    if match:
        keys = [
            "fallback_node_slack", "fallback_equal_score",
            "fallback_disabled_no_landmarks", "fallback_non_searchable",
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


def run_config(args, configs, domain, problem, config_name, search, repetition):
    task = task_name(problem, args.benchmark_dir)
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
        stdout += "\nBenchmark harness timeout after %s seconds.\n" % (
            args.timeout + 120)
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
        "config_preset": args.config_preset,
        "num_configs": len(configs),
        "log": os.path.relpath(log_path, REPO_ROOT),
        "plan": os.path.relpath(plan_path, REPO_ROOT),
    })
    remove_generated_files()
    return row


def as_float(row, field):
    try:
        return float(row[field])
    except (KeyError, TypeError, ValueError):
        return None


def as_int(row, field):
    try:
        return int(row[field])
    except (KeyError, TypeError, ValueError):
        return 0


def is_solved(row):
    return str(row.get("returncode")) == "0" and bool(row.get("plan_cost"))


def geometric_mean(values):
    values = [value for value in values if value is not None and value > 0]
    if not values:
        return None
    return math.exp(sum(math.log(value) for value in values) / len(values))


def arithmetic_mean(values):
    values = [value for value in values if value is not None]
    if not values:
        return None
    return sum(values) / len(values)


def format_number(value, suffix=""):
    if value is None:
        return "n/a"
    return "%.6g%s" % (value, suffix)


def summarize(rows, configs):
    config_names = [name for name, _ in configs]
    lines = []
    lines.append("SLBD comparison summary")
    lines.append("=======================")
    lines.append("")
    lines.append("Rows: %d" % len(rows))
    lines.append("Configs: %s" % ", ".join(config_names))
    lines.append("")

    lines.append("Solved count")
    for config in config_names:
        config_rows = [row for row in rows if row["config"] == config]
        solved = sum(1 for row in config_rows if is_solved(row))
        lines.append("  %s: %d/%d" % (config, solved, len(config_rows)))
    lines.append("")

    lines.append("Return codes")
    for config in config_names:
        codes = Counter(
            row["returncode"] for row in rows if row["config"] == config)
        code_text = ", ".join(
            "%s=%d" % (code, count) for code, count in sorted(codes.items()))
        lines.append("  %s: %s" % (config, code_text or "none"))
    lines.append("")

    cost_mismatches = []
    rows_by_task_repeat = defaultdict(list)
    for row in rows:
        rows_by_task_repeat[(row["task"], row["repeat"])].append(row)
    for (task, repeat), group in sorted(rows_by_task_repeat.items()):
        solved_costs = {
            row["config"]: row["plan_cost"] for row in group if is_solved(row)
        }
        if len(set(solved_costs.values())) > 1:
            cost_mismatches.append((task, repeat, solved_costs))

    lines.append("Cost mismatches")
    if cost_mismatches:
        for task, repeat, costs in cost_mismatches[:20]:
            cost_text = ", ".join(
                "%s=%s" % (config, cost)
                for config, cost in sorted(costs.items()))
            lines.append("  %s repeat %s: %s" % (task, repeat, cost_text))
        if len(cost_mismatches) > 20:
            lines.append("  ... %d more" % (len(cost_mismatches) - 20))
    else:
        lines.append("  none")
    lines.append("")

    common_groups = []
    for key, group in rows_by_task_repeat.items():
        by_config = {row["config"]: row for row in group}
        if all(config in by_config and is_solved(by_config[config])
               for config in config_names):
            common_groups.append(by_config)

    lines.append("Common-solved metrics")
    lines.append("  task-repeat groups: %d" % len(common_groups))
    common_search_times = defaultdict(list)
    common_wall_times = defaultdict(list)
    common_memory = defaultdict(list)
    for by_config in common_groups:
        for config in config_names:
            row = by_config[config]
            common_search_times[config].append(
                as_float(row, "actual_search_time"))
            common_wall_times[config].append(as_float(row, "wall_time"))
            common_memory[config].append(as_float(row, "peak_memory_kb"))

    baseline = config_names[0] if config_names else None
    baseline_gm = geometric_mean(common_search_times[baseline]) if baseline else None
    for config in config_names:
        gm = geometric_mean(common_search_times[config])
        wall = arithmetic_mean(common_wall_times[config])
        memory = arithmetic_mean(common_memory[config])
        ratio = gm / baseline_gm if gm and baseline_gm else None
        lines.append(
            "  %s: search_gm=%s, ratio_vs_%s=%s, wall_avg=%s, memory_avg=%s KB" %
            (config, format_number(gm, "s"), baseline,
             format_number(ratio), format_number(wall, "s"),
             format_number(memory)))
    lines.append("")

    lines.append("Per-domain search-time ratios vs %s" % baseline)
    by_domain_config = defaultdict(list)
    for by_config in common_groups:
        domain = task_domain(next(iter(by_config.values()))["task"])
        for config in config_names:
            by_domain_config[(domain, config)].append(
                as_float(by_config[config], "actual_search_time"))
    domains = sorted({domain for domain, _ in by_domain_config})
    for domain in domains:
        baseline_domain_gm = geometric_mean(by_domain_config[(domain, baseline)])
        parts = []
        for config in config_names:
            gm = geometric_mean(by_domain_config[(domain, config)])
            ratio = gm / baseline_domain_gm if gm and baseline_domain_gm else None
            parts.append("%s=%s" % (config, format_number(ratio)))
        lines.append("  %s: %s" % (domain, ", ".join(parts)))
    lines.append("")

    lines.append("Guidance and fallback rates")
    for config in config_names:
        group = [row for row in rows if row["config"] == config]
        guidance_total = sum(as_int(row, "guidance_total") for row in group)
        guidance_chosen = sum(
            as_int(row, "guidance_forward") + as_int(row, "guidance_backward")
            for row in group)
        fallback_total = sum(as_int(row, "fallback_to_bdd_nodes") for row in group)
        guidance_rate = (
            100.0 * guidance_chosen / guidance_total if guidance_total else None)
        fallback_rate = (
            100.0 * fallback_total / guidance_total if guidance_total else None)
        lines.append(
            "  %s: guided=%d/%d (%s%%), fallback_to_bdd_nodes=%d (%s%%), "
            "node_slack=%d, equal_score=%d, disabled_no_landmarks=%d, "
            "non_searchable=%d, coverage_recomputations=%d" %
            (config, guidance_chosen, guidance_total,
             format_number(guidance_rate), fallback_total,
             format_number(fallback_rate),
             sum(as_int(row, "fallback_node_slack") for row in group),
             sum(as_int(row, "fallback_equal_score") for row in group),
             sum(as_int(row, "fallback_disabled_no_landmarks") for row in group),
             sum(as_int(row, "fallback_non_searchable") for row in group),
             sum(as_int(row, "coverage_recomputations") for row in group)))

    return "\n".join(lines) + "\n"


def ensure_parent_dir(path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def main():
    args = parse_args()
    args.benchmark_dir = os.path.abspath(args.benchmark_dir)
    configs = get_configs(args)
    os.makedirs(args.log_dir, exist_ok=True)
    ensure_parent_dir(args.output)
    ensure_parent_dir(args.summary)
    tasks = args.task or discover_tasks(
        args.benchmark_dir, args.include_domain, args.tasks_per_domain)
    rows = []

    for domain, problem in tasks:
        for repetition in range(1, args.repeat + 1):
            for config_name, search in configs:
                print("%s %s repeat %s" % (
                    task_name(problem, args.benchmark_dir),
                    config_name,
                    repetition))
                rows.append(run_config(
                    args, configs, domain, problem, config_name, search,
                    repetition))

    fieldnames = [
        "task", "config", "repeat", "config_preset", "num_configs",
        "returncode", "plan_cost", "plan_length", "actual_search_time",
        "wall_time", "peak_memory_kb", "fw_time", "bw_time", "fw_steps",
        "bw_steps", "landmarks_total", "landmarks_simple",
        "landmarks_disjunctive", "landmarks_conjunctive",
        "landmarks_min_cost_sum", "lm_guidance_score",
        "coverage_forward_unweighted", "coverage_forward_weighted",
        "coverage_backward_unweighted", "coverage_backward_weighted",
        "guidance_forward", "guidance_backward", "fallback_to_bdd_nodes",
        "guidance_total", "guidance_evaluated", "coverage_recomputations",
        "fallback_node_slack", "fallback_equal_score",
        "fallback_disabled_no_landmarks", "fallback_non_searchable",
        "search", "log", "plan",
    ]
    with open(args.output, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print("Wrote %s" % args.output)

    summary = summarize(rows, configs)
    with open(args.summary, "w", encoding="utf-8") as summary_file:
        summary_file.write(summary)
    print(summary)
    print("Wrote %s" % args.summary)


if __name__ == "__main__":
    main()
