#! /usr/bin/env python3

import argparse
import csv
import math
import os
import re
import signal
import subprocess
import sys
import time
from collections import Counter, defaultdict


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_BENCHMARK_DIR = os.path.join(REPO_ROOT, "misc", "tests", "benchmarks")
DEFAULT_RESULTS_DIR = os.path.join(REPO_ROOT, "slbd-results")

# Oracle diagnostic: fully UNMUZZLE guidance (gate wide open, every decision
# overridden whenever landmarks have a non-tied opinion) and compare following
# the landmark direction against following its opposite (polarity flip). If
# "follow" and "anti" straddle sbd, the landmark direction signal carries real
# information (possibly inverted); if both are ~equally worse than sbd, the
# signal is noise and per-step landmark direction guidance should be abandoned
# in favour of a different signal. This changes NOTHING about defaults.
_ORACLE_OPEN = (
    "lm_node_slack_absolute=-1,lm_node_slack_percent=1000000000,"
    "lm_guidance_max_overrides_percent=100,lm_min_score_gap=1,"
    "lm_guidance_start_decision=1,lm_eval_frequency=1")

CONFIG_PRESETS = {
    "oracle-meet": [
        ("sbd", "sbd()"),
        ("meet-follow",
         "slbd(lm_factory=lm_rhw(),lm_guidance_score=meet_bdd,"
         "lm_guidance_polarity=less_covered," + _ORACLE_OPEN + ")"),
        ("meet-anti",
         "slbd(lm_factory=lm_rhw(),lm_guidance_score=meet_bdd,"
         "lm_guidance_polarity=more_covered," + _ORACLE_OPEN + ")"),
        ("balance-follow",
         "slbd(lm_factory=lm_rhw(),lm_guidance_score=balance_bdd,"
         "lm_guidance_polarity=less_covered," + _ORACLE_OPEN + ")"),
        ("balance-anti",
         "slbd(lm_factory=lm_rhw(),lm_guidance_score=balance_bdd,"
         "lm_guidance_polarity=more_covered," + _ORACLE_OPEN + ")"),
    ],
    "oracle": [
        ("sbd", "sbd()"),
        ("slbd-no-guidance", "slbd(lm_guidance=false,lm_factory=lm_rhw())"),
        ("oracle-follow-weighted",
         "slbd(lm_factory=lm_rhw(),lm_guidance_score=weighted,"
         "lm_guidance_polarity=less_covered," + _ORACLE_OPEN + ")"),
        ("oracle-anti-weighted",
         "slbd(lm_factory=lm_rhw(),lm_guidance_score=weighted,"
         "lm_guidance_polarity=more_covered," + _ORACLE_OPEN + ")"),
        ("oracle-follow-agenda",
         "slbd(lm_factory=lm_rhw(),lm_guidance_score=agenda,"
         "lm_guidance_polarity=less_covered," + _ORACLE_OPEN + ")"),
        ("oracle-anti-agenda",
         "slbd(lm_factory=lm_rhw(),lm_guidance_score=agenda,"
         "lm_guidance_polarity=more_covered," + _ORACLE_OPEN + ")"),
    ],
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
    "frontier": [
        ("sbd", "sbd()"),
        ("slbd-no-guidance", "slbd(lm_guidance=false,lm_factory=lm_rhw())"),
        ("slbd", "slbd(lm_factory=lm_rhw())"),
        ("slbd-safe-eval10",
         "slbd(lm_factory=lm_rhw(),lm_node_slack_percent=0,lm_eval_frequency=10)"),
        ("slbd-frontier",
         "slbd(lm_factory=lm_rhw(),lm_guidance_scope=frontier)"),
        ("slbd-frontier-safe-eval10",
         "slbd(lm_factory=lm_rhw(),lm_guidance_scope=frontier,"
         "lm_node_slack_percent=0,lm_eval_frequency=10)"),
        ("slbd-frontier-abs5-eval10",
         "slbd(lm_factory=lm_rhw(),lm_guidance_scope=frontier,"
         "lm_node_slack_absolute=5,lm_eval_frequency=10)"),
        ("slbd-frontier-weighted-abs5-eval10",
         "slbd(lm_factory=lm_rhw(),lm_guidance_scope=frontier,"
         "lm_guidance_score=weighted,lm_node_slack_absolute=5,"
         "lm_eval_frequency=10)"),
        ("slbd-lazy-frontier-abs5-eval10",
         "slbd(lm_factory=lm_rhw(),lm_lazy_landmarks=true,"
         "lm_guidance_scope=frontier,lm_node_slack_absolute=5,"
         "lm_eval_frequency=10)"),
    ],
    "ordered": [
        ("sbd", "sbd()"),
        ("slbd-no-guidance", "slbd(lm_guidance=false,lm_factory=lm_rhw())"),
        ("slbd", "slbd(lm_factory=lm_rhw())"),
        ("slbd-safe-eval10",
         "slbd(lm_factory=lm_rhw(),lm_node_slack_percent=0,lm_eval_frequency=10)"),
        ("slbd-frontier-abs5-eval10",
         "slbd(lm_factory=lm_rhw(),lm_guidance_scope=frontier,"
         "lm_node_slack_absolute=5,lm_eval_frequency=10)"),
        ("slbd-ordered-frontier-more-abs5-eval10",
         "slbd(lm_factory=lm_rhw(),lm_guidance_score=ordered,"
         "lm_guidance_polarity=more_covered,lm_guidance_scope=frontier,"
         "lm_node_slack_absolute=5,lm_eval_frequency=10)"),
        ("slbd-ordered-seen-more-abs5-eval10",
         "slbd(lm_factory=lm_rhw(),lm_guidance_score=ordered,"
         "lm_guidance_polarity=more_covered,lm_guidance_scope=seen,"
         "lm_node_slack_absolute=5,lm_eval_frequency=10)"),
        ("slbd-meeting-frontier-more-abs5-eval10",
         "slbd(lm_factory=lm_rhw(),lm_guidance_score=meeting,"
         "lm_guidance_polarity=more_covered,lm_guidance_scope=frontier,"
         "lm_node_slack_absolute=5,lm_eval_frequency=10)"),
        ("slbd-meeting-frontier-more-abs10-eval10",
         "slbd(lm_factory=lm_rhw(),lm_guidance_score=meeting,"
         "lm_guidance_polarity=more_covered,lm_guidance_scope=frontier,"
         "lm_node_slack_absolute=10,lm_eval_frequency=10)"),
        ("slbd-lazy-ordered-frontier-more-abs5-eval10",
         "slbd(lm_factory=lm_rhw(),lm_lazy_landmarks=true,"
         "lm_guidance_score=ordered,lm_guidance_polarity=more_covered,"
         "lm_guidance_scope=frontier,lm_node_slack_absolute=5,"
         "lm_eval_frequency=10)"),
        ("slbd-lazy-meeting-frontier-more-abs5-eval10",
         "slbd(lm_factory=lm_rhw(),lm_lazy_landmarks=true,"
         "lm_guidance_score=meeting,lm_guidance_polarity=more_covered,"
         "lm_guidance_scope=frontier,lm_node_slack_absolute=5,"
         "lm_eval_frequency=10)"),
    ],
    "agenda": [
        ("sbd", "sbd()"),
        ("slbd-no-guidance", "slbd(lm_guidance=false,lm_factory=lm_rhw())"),
        ("slbd", "slbd(lm_factory=lm_rhw())"),
        ("slbd-safe-eval10",
         "slbd(lm_factory=lm_rhw(),lm_node_slack_percent=0,lm_eval_frequency=10)"),
        ("slbd-meeting-frontier-more-abs5-eval10",
         "slbd(lm_factory=lm_rhw(),lm_guidance_score=meeting,"
         "lm_guidance_polarity=more_covered,lm_guidance_scope=frontier,"
         "lm_node_slack_absolute=5,lm_eval_frequency=10)"),
        ("slbd-agenda-more-abs5-eval10",
         "slbd(lm_factory=lm_rhw(),lm_guidance_score=agenda,"
         "lm_guidance_polarity=more_covered,lm_node_slack_absolute=5,"
         "lm_eval_frequency=10)"),
        ("slbd-agenda-more-abs10-eval10",
         "slbd(lm_factory=lm_rhw(),lm_guidance_score=agenda,"
         "lm_guidance_polarity=more_covered,lm_node_slack_absolute=10,"
         "lm_eval_frequency=10)"),
        ("slbd-agenda-weighted-more-abs5-eval10",
         "slbd(lm_factory=lm_rhw(),lm_guidance_score=agenda_weighted,"
         "lm_guidance_polarity=more_covered,lm_node_slack_absolute=5,"
         "lm_eval_frequency=10)"),
        ("slbd-agenda-meeting-more-abs5-eval10",
         "slbd(lm_factory=lm_rhw(),lm_guidance_score=agenda_meeting,"
         "lm_guidance_polarity=more_covered,lm_node_slack_absolute=5,"
         "lm_eval_frequency=10)"),
        ("slbd-agenda-more-gap2-abs5-eval10",
         "slbd(lm_factory=lm_rhw(),lm_guidance_score=agenda,"
         "lm_guidance_polarity=more_covered,lm_node_slack_absolute=5,"
         "lm_eval_frequency=10,lm_min_score_gap=2)"),
        ("slbd-lazy-agenda-more-abs5-eval10",
         "slbd(lm_factory=lm_rhw(),lm_lazy_landmarks=true,"
         "lm_guidance_score=agenda,lm_guidance_polarity=more_covered,"
         "lm_node_slack_absolute=5,lm_eval_frequency=10)"),
    ],
}

GENERATED_FILES = ["output", "output.sas", "sas_plan"]

FIELDNAMES = [
    "task", "config", "repeat", "config_preset", "num_configs",
    "returncode", "plan_cost", "plan_length", "actual_search_time",
    "wall_time", "peak_memory_kb", "fw_time", "bw_time", "fw_steps",
    "bw_steps", "landmarks_total", "landmarks_simple",
    "landmarks_disjunctive", "landmarks_conjunctive",
    "landmarks_min_cost_sum", "lm_guidance_score",
    "lm_guidance_scope", "lm_guidance_polarity",
    "lm_landmark_filter", "lm_min_score_gap", "lm_node_slack_absolute",
    "lm_guidance_start_decision",
    "lm_guidance_max_overrides_percent", "lm_lazy_landmarks",
    "lm_landmarks_initialized", "lm_landmark_initializations",
    "coverage_forward_unweighted", "coverage_forward_weighted",
    "coverage_backward_unweighted", "coverage_backward_weighted",
    "ordered_forward_unweighted", "ordered_forward_weighted",
    "ordered_backward_unweighted", "ordered_backward_weighted",
    "meeting_forward_unweighted", "meeting_forward_weighted",
    "meeting_backward_unweighted", "meeting_backward_weighted",
    "agenda_forward_unweighted", "agenda_forward_weighted",
    "agenda_backward_unweighted", "agenda_backward_weighted",
    "agenda_meeting_forward_unweighted", "agenda_meeting_forward_weighted",
    "agenda_meeting_backward_unweighted", "agenda_meeting_backward_weighted",
    "guidance_forward", "guidance_backward", "fallback_to_bdd_nodes",
    "guidance_total", "guidance_evaluated", "coverage_recomputations",
    "fallback_node_slack", "fallback_equal_score",
    "fallback_disabled_no_landmarks", "fallback_non_searchable",
    "fallback_insufficient_score_gap", "fallback_warmup",
    "fallback_override_budget", "fallback_frontier_unavailable",
    "search", "log", "plan",
]


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
        "--config-preset",
        choices=[
            "smoke", "tuning", "confirm", "frontier", "ordered", "agenda",
            "oracle", "oracle-meet"],
        default="smoke",
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
    parser.add_argument(
        "--resume", action="store_true",
        help="reuse matching rows already present in --output and skip them")
    parser.add_argument(
        "--summary-only", action="store_true",
        help="write --summary from matching rows in --output without running planners")
    parser.add_argument(
        "--max-runs", type=int,
        help="stop after this many newly executed planner runs")
    parser.add_argument(
        "--extra-config", action="append", default=[], metavar="NAME=SEARCH",
        help="additional config to run; may be used multiple times")
    return parser.parse_args()


def get_configs(args):
    if args.config_preset == "confirm":
        configs = [
            ("sbd", "sbd()"),
            ("slbd-no-guidance", "slbd(lm_guidance=false,lm_factory=lm_rhw())"),
            ("slbd", "slbd(lm_factory=lm_rhw())"),
            (args.confirm_candidate_name, args.confirm_candidate),
        ]
    else:
        configs = list(CONFIG_PRESETS[args.config_preset])

    for extra_config in args.extra_config:
        if "=" not in extra_config:
            raise ValueError(
                "--extra-config must use NAME=SEARCH format: %s" %
                extra_config)
        name, search = extra_config.split("=", 1)
        name = name.strip()
        search = search.strip()
        if not name or not search:
            raise ValueError(
                "--extra-config must use NAME=SEARCH format: %s" %
                extra_config)
        configs.append((name, search))
    names = [name for name, _ in configs]
    duplicate_names = sorted(
        name for name, count in Counter(names).items() if count > 1)
    if duplicate_names:
        raise ValueError(
            "duplicate config names are not allowed: %s" %
            ", ".join(duplicate_names))
    return configs


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
        "lm_guidance_scope": "",
        "lm_guidance_polarity": "",
        "lm_landmark_filter": "",
        "lm_min_score_gap": "",
        "lm_node_slack_absolute": "",
        "lm_guidance_start_decision": "",
        "lm_guidance_max_overrides_percent": "",
        "lm_lazy_landmarks": "",
        "lm_landmarks_initialized": "",
        "lm_landmark_initializations": "",
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
        "fallback_insufficient_score_gap": "",
        "fallback_warmup": "",
        "fallback_override_budget": "",
        "fallback_frontier_unavailable": "",
        "coverage_forward_unweighted": "",
        "coverage_forward_weighted": "",
        "coverage_backward_unweighted": "",
        "coverage_backward_weighted": "",
        "ordered_forward_unweighted": "",
        "ordered_forward_weighted": "",
        "ordered_backward_unweighted": "",
        "ordered_backward_weighted": "",
        "meeting_forward_unweighted": "",
        "meeting_forward_weighted": "",
        "meeting_backward_unweighted": "",
        "meeting_backward_weighted": "",
        "agenda_forward_unweighted": "",
        "agenda_forward_weighted": "",
        "agenda_backward_unweighted": "",
        "agenda_backward_weighted": "",
        "agenda_meeting_forward_unweighted": "",
        "agenda_meeting_forward_weighted": "",
        "agenda_meeting_backward_unweighted": "",
        "agenda_meeting_backward_weighted": "",
    }

    patterns = [
        ("plan_cost", r"Plan cost: (\d+)"),
        ("plan_length", r"Plan length: (\d+) step"),
        ("actual_search_time", r"Actual search time: ([0-9.eE+-]+)s"),
        ("lm_guidance_score", r"Landmark guidance score: ([A-Za-z0-9_-]+)"),
        ("lm_guidance_scope", r"Landmark guidance scope: ([A-Za-z0-9_-]+)"),
        ("lm_guidance_polarity", r"Landmark guidance polarity: ([A-Za-z0-9_-]+)"),
        ("lm_landmark_filter", r"Landmark guidance filter: ([A-Za-z0-9_-]+)"),
        ("lm_min_score_gap", r"Landmark guidance min score gap: ([0-9]+)"),
        ("lm_node_slack_absolute", r"Landmark guidance node slack absolute: (-?[0-9]+)"),
        ("lm_guidance_start_decision", r"Landmark guidance start decision: ([0-9]+)"),
        ("lm_guidance_max_overrides_percent",
         r"Landmark guidance max overrides percent: ([0-9]+)"),
        ("lm_lazy_landmarks", r"Landmark guidance lazy landmarks: (true|false)"),
        ("lm_landmarks_initialized",
         r"Landmark guidance landmarks initialized: (true|false)"),
        ("lm_landmark_initializations",
         r"Landmark guidance landmark initializations: ([0-9]+)"),
    ]
    for key, pattern in patterns:
        matches = re.findall(pattern, stdout)
        if matches:
            result[key] = matches[-1]

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
        r"non_searchable=([0-9]+)"
        r"(?:, insufficient_score_gap=([0-9]+))?"
        r"(?:, warmup=([0-9]+))?"
        r"(?:, override_budget=([0-9]+))?"
        r"(?:, frontier_unavailable=([0-9]+))?",
        stdout)
    if match:
        keys = [
            "fallback_node_slack", "fallback_equal_score",
            "fallback_disabled_no_landmarks", "fallback_non_searchable",
            "fallback_insufficient_score_gap", "fallback_warmup",
            "fallback_override_budget", "fallback_frontier_unavailable",
        ]
        result.update({
            key: value or "" for key, value in zip(keys, match.groups())
        })

    for direction in ["forward", "backward"]:
        match = re.search(
            r"Landmark coverage %s: unweighted=([0-9]+/[0-9]+), "
            r"weighted=([0-9]+/[0-9]+)" % direction,
            stdout)
        if match:
            result["coverage_%s_unweighted" % direction] = match.group(1)
            result["coverage_%s_weighted" % direction] = match.group(2)

    for score_name, field_prefix in [
        ("ordered score", "ordered"),
        ("meeting score", "meeting"),
        ("agenda score", "agenda"),
        ("agenda meeting score", "agenda_meeting"),
    ]:
        for direction in ["forward", "backward"]:
            match = re.search(
                r"Landmark %s %s: unweighted=([0-9]+/[0-9]+), "
                r"weighted=([0-9]+/[0-9]+)" % (score_name, direction),
                stdout)
            if match:
                result["%s_%s_unweighted" % (
                    field_prefix, direction)] = match.group(1)
                result["%s_%s_weighted" % (
                    field_prefix, direction)] = match.group(2)

    return result


def run_planner_command(cmd, timeout):
    popen_kwargs = {
        "cwd": REPO_ROOT,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "universal_newlines": True,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(cmd, **popen_kwargs)
    try:
        stdout, _ = proc.communicate(timeout=timeout)
        return stdout, proc.returncode, False
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        try:
            stdout, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
            stdout, _ = proc.communicate()
        return stdout or "", -1, True


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
    stdout, returncode, timed_out = run_planner_command(
        cmd,
        args.timeout + 120)
    if timed_out:
        stdout += "\nBenchmark harness timeout after %s seconds.\n" % (
            args.timeout + 120)
    elapsed = time.time() - start_time

    with open(log_path, "w", encoding="utf-8") as log_file:
        log_file.write("$ %s\n\n" % " ".join(cmd))
        log_file.write(stdout)

    row = parse_output(stdout, returncode, elapsed)
    row.update({
        "task": task,
        "config": config_name,
        "search": search,
        "repeat": str(repetition),
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


def fraction_numerator(value):
    try:
        return int(str(value).split("/", 1)[0])
    except (TypeError, ValueError):
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


def summarize(rows, configs, planned_run_count=None):
    config_names = [name for name, _ in configs]
    lines = []
    lines.append("SLBD comparison summary")
    lines.append("=======================")
    lines.append("")
    lines.append("Rows: %d" % len(rows))
    if planned_run_count is not None:
        lines.append("Planned runs: %d" % planned_run_count)
        lines.append("Completed rows: %d" % len(rows))
        lines.append("Missing rows: %d" % max(0, planned_run_count - len(rows)))
    lines.append("Configs: %s" % ", ".join(config_names))
    lines.append("")

    lines.append("Solved count")
    solved_counts = {}
    total_counts = {}
    for config in config_names:
        config_rows = [row for row in rows if row["config"] == config]
        solved = sum(1 for row in config_rows if is_solved(row))
        solved_counts[config] = solved
        total_counts[config] = len(config_rows)
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

    baseline = "sbd" if "sbd" in config_names else (
        config_names[0] if config_names else None)
    baseline_gm = geometric_mean(common_search_times[baseline]) if baseline else None
    common_ratios = {}
    for config in config_names:
        gm = geometric_mean(common_search_times[config])
        wall = arithmetic_mean(common_wall_times[config])
        memory = arithmetic_mean(common_memory[config])
        ratio = gm / baseline_gm if gm and baseline_gm else None
        common_ratios[config] = ratio
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
    domain_ratios = {}
    for domain in domains:
        baseline_domain_gm = geometric_mean(by_domain_config[(domain, baseline)])
        parts = []
        for config in config_names:
            gm = geometric_mean(by_domain_config[(domain, config)])
            ratio = gm / baseline_domain_gm if gm and baseline_domain_gm else None
            domain_ratios[(domain, config)] = ratio
            parts.append("%s=%s" % (config, format_number(ratio)))
        lines.append("  %s: %s" % (domain, ", ".join(parts)))
    lines.append("")

    lines.append("Best per domain")
    if domains:
        for domain in domains:
            candidates = [
                (domain_ratios[(domain, config)], config)
                for config in config_names
                if config != baseline and domain_ratios.get((domain, config))
            ]
            if candidates:
                ratio, config = min(candidates)
                lines.append(
                    "  %s: %s ratio_vs_%s=%s" %
                    (domain, config, baseline, format_number(ratio)))
            else:
                lines.append("  %s: n/a" % domain)
    else:
        lines.append("  n/a")
    lines.append("")

    lines.append("Acceptance gate")
    gate_complete = planned_run_count is None or len(rows) >= planned_run_count
    reference_configs = [
        config for config in ["sbd", "slbd-no-guidance", "slbd"]
        if config in config_names
    ]
    solved_required = (
        max(solved_counts[config] for config in reference_configs)
        if reference_configs else None)
    cost_ok = not cost_mismatches
    lines.append(
        "  references: %s" %
        (", ".join(reference_configs) if reference_configs else "n/a"))
    if not gate_complete:
        lines.append("  status: incomplete; final pass/fail waits for all planned rows")
    for config in config_names:
        if config == baseline:
            continue
        solved_ok = (
            solved_required is not None and
            solved_counts[config] >= solved_required)
        speed_ok = (
            common_ratios.get(config) is not None and
            common_ratios[config] <= 0.9)
        overall_ok = cost_ok and solved_ok and speed_ok
        overall_text = (
            "PASS" if overall_ok else "FAIL") if gate_complete else "INCOMPLETE"
        lines.append(
            "  %s: cost=%s, solved=%s (%d/%d, required %s), "
            "speed=%s (ratio_vs_%s=%s), overall=%s" %
            (config,
             "pass" if cost_ok else "fail",
             "pass" if solved_ok else "fail",
             solved_counts[config],
             total_counts[config],
             solved_required if solved_required is not None else "n/a",
             "pass" if speed_ok else "fail",
             baseline,
             format_number(common_ratios.get(config)),
             overall_text))
    lines.append("")

    lines.append("Lazy landmark initialization")
    for config in config_names:
        group = [row for row in rows if row["config"] == config]
        lazy_rows = sum(
            1 for row in group if row.get("lm_lazy_landmarks") == "true")
        initialized_rows = sum(
            1 for row in group
            if row.get("lm_landmarks_initialized") == "true")
        initializations = sum(
            as_int(row, "lm_landmark_initializations") for row in group)
        lines.append(
            "  %s: lazy_runs=%d/%d, initialized_runs=%d/%d, "
            "initializations=%d" %
            (config, lazy_rows, len(group), initialized_rows, len(group),
             initializations))
    lines.append("")

    lines.append("Landmark score diagnostics")
    for config in config_names:
        group = [row for row in rows if row["config"] == config]
        lines.append(
            "  %s: ordered_fw=%d, ordered_bw=%d, meeting_fw=%d, "
            "meeting_bw=%d, agenda_fw=%d, agenda_bw=%d, "
            "agenda_meeting_fw=%d, agenda_meeting_bw=%d" %
            (config,
             sum(fraction_numerator(row.get("ordered_forward_unweighted"))
                 for row in group),
             sum(fraction_numerator(row.get("ordered_backward_unweighted"))
                 for row in group),
             sum(fraction_numerator(row.get("meeting_forward_unweighted"))
                 for row in group),
             sum(fraction_numerator(row.get("meeting_backward_unweighted"))
                 for row in group),
             sum(fraction_numerator(row.get("agenda_forward_unweighted"))
                 for row in group),
             sum(fraction_numerator(row.get("agenda_backward_unweighted"))
                 for row in group),
             sum(fraction_numerator(row.get("agenda_meeting_forward_unweighted"))
                 for row in group),
             sum(fraction_numerator(row.get("agenda_meeting_backward_unweighted"))
                 for row in group)))
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
            "non_searchable=%d, insufficient_score_gap=%d, "
            "warmup=%d, override_budget=%d, frontier_unavailable=%d, "
            "coverage_recomputations=%d" %
            (config, guidance_chosen, guidance_total,
             format_number(guidance_rate), fallback_total,
             format_number(fallback_rate),
             sum(as_int(row, "fallback_node_slack") for row in group),
             sum(as_int(row, "fallback_equal_score") for row in group),
             sum(as_int(row, "fallback_disabled_no_landmarks") for row in group),
             sum(as_int(row, "fallback_non_searchable") for row in group),
             sum(as_int(row, "fallback_insufficient_score_gap") for row in group),
             sum(as_int(row, "fallback_warmup") for row in group),
             sum(as_int(row, "fallback_override_budget") for row in group),
             sum(as_int(row, "fallback_frontier_unavailable") for row in group),
             sum(as_int(row, "coverage_recomputations") for row in group)))

    return "\n".join(lines) + "\n"


def ensure_parent_dir(path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def row_for_csv(row):
    csv_row = {}
    for field in FIELDNAMES:
        value = row.get(field, "")
        csv_row[field] = "" if value is None else str(value)
    return csv_row


def row_key(task, config, repeat, search):
    return (task, config, str(repeat), search)


def row_key_from_row(row):
    return row_key(
        row.get("task", ""),
        row.get("config", ""),
        row.get("repeat", ""),
        row.get("search", ""))


def iter_runs(args, configs, tasks):
    for domain, problem in tasks:
        task = task_name(problem, args.benchmark_dir)
        for repetition in range(1, args.repeat + 1):
            for config_name, search in configs:
                yield {
                    "domain": domain,
                    "problem": problem,
                    "task": task,
                    "config": config_name,
                    "search": search,
                    "repeat": repetition,
                }


def read_existing_rows(path, planned_keys):
    if not os.path.exists(path):
        return []
    rows_by_key = {}
    with open(path, newline="", encoding="utf-8") as csv_file:
        for row in csv.DictReader(csv_file):
            if row_key_from_row(row) in planned_keys:
                rows_by_key[row_key_from_row(row)] = row
    return list(rows_by_key.values())


def write_rows(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(row_for_csv(row))


def append_row(path, row):
    with open(path, "a", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES)
        writer.writerow(row_for_csv(row))
        csv_file.flush()
        os.fsync(csv_file.fileno())


def write_summary(path, rows, configs, planned_run_count=None):
    summary = summarize(rows, configs, planned_run_count)
    with open(path, "w", encoding="utf-8") as summary_file:
        summary_file.write(summary)
    return summary


def main():
    args = parse_args()
    args.benchmark_dir = os.path.abspath(args.benchmark_dir)
    configs = get_configs(args)
    os.makedirs(args.log_dir, exist_ok=True)
    ensure_parent_dir(args.output)
    ensure_parent_dir(args.summary)
    tasks = args.task or discover_tasks(
        args.benchmark_dir, args.include_domain, args.tasks_per_domain)
    runs = list(iter_runs(args, configs, tasks))
    planned_keys = {
        row_key(run["task"], run["config"], run["repeat"], run["search"])
        for run in runs
    }

    rows = read_existing_rows(args.output, planned_keys) if (
        args.resume or args.summary_only) else []
    completed_keys = {row_key_from_row(row) for row in rows}
    write_rows(args.output, rows)
    print("Wrote %s" % args.output)

    if args.summary_only:
        summary = write_summary(args.summary, rows, configs, len(runs))
        print(summary)
        print("Wrote %s" % args.summary)
        return

    executed_runs = 0
    for run in runs:
        key = row_key(run["task"], run["config"], run["repeat"], run["search"])
        if key in completed_keys:
            print("%s %s repeat %s skipped" % (
                run["task"], run["config"], run["repeat"]))
            continue

        print("%s %s repeat %s" % (
            run["task"], run["config"], run["repeat"]))
        row = run_config(
            args, configs, run["domain"], run["problem"], run["config"],
            run["search"], run["repeat"])
        row = row_for_csv(row)
        rows.append(row)
        completed_keys.add(key)
        append_row(args.output, row)
        write_summary(args.summary, rows, configs, len(runs))
        executed_runs += 1
        if args.max_runs is not None and executed_runs >= args.max_runs:
            break

    summary = write_summary(args.summary, rows, configs, len(runs))
    print(summary)
    print("Wrote %s" % args.summary)


if __name__ == "__main__":
    main()
