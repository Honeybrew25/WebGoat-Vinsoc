#!/usr/bin/env python3
"""Evaluate Stage 8 optimization profiles without mutating the Stage 4-7 baseline."""

import argparse
import collections
import json
import os
import pathlib
import re
import statistics
import sys

import yaml

sys.stdout.reconfigure(encoding="utf-8", newline="\n")

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))

from stage5_normalize import cost_for_run, normalize_result  # noqa: E402
from stage6_dedup import dedup_tool, norm_title  # noqa: E402
from stage7_judge import in_scope, record_key  # noqa: E402

_CFG = _ROOT / "config" / "benchmark.yaml"
_BASE_JUDGED = (
    _ROOT / "results" / "findings" / "normalized" / "judged-independent.jsonl"
)
_BASE_COST = _ROOT / "results" / "stats" / "cost_by_run.json"
_LESSON = re.compile(r"lessons/([^/]+)/")
_RESOURCE_KEYS = ("wall_clock_s", "total_tokens", "cost_usd")


def load_cfg():
    with _CFG.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def read_jsonl(path):
    path = pathlib.Path(path)
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def write_jsonl(path, rows):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def match_baseline(candidate, baseline, tolerance):
    matches = []
    for old in baseline:
        if old["tool"] != candidate["tool"] or old["file"] != candidate["file"]:
            continue
        if norm_title(old["title"]) != norm_title(candidate["title"]):
            continue
        both_exact = (
            old.get("line_confidence")
            == candidate.get("line_confidence")
            == "exact"
        )
        if both_exact and abs(
            (old.get("line_min") or 0) - (candidate.get("line_min") or 0)
        ) > tolerance:
            continue
        matches.append(old)
    if not matches:
        return None
    return min(
        matches,
        key=lambda row: abs(
            (row.get("line_min") or 0) - (candidate.get("line_min") or 0)
        ),
    )


def budget_allows(actual, projected, reserve, cap):
    return actual + projected + reserve <= cap


def projected_run_cost(baseline_rows, tool, run_index):
    prefix = f"run-{run_index:02d}-"
    matches = [
        row["cost_usd"]
        for row in baseline_rows
        if row["tool"] == tool and row["run"].startswith(prefix)
    ]
    if not matches and run_index > 1:
        matches = [
            row["cost_usd"]
            for row in baseline_rows
            if row["tool"] == tool and row.get("phase") == "warm"
        ]
    if not matches:
        matches = [
            row["cost_usd"] for row in baseline_rows if row["tool"] == tool
        ]
    if not matches:
        raise ValueError(f"no baseline cost for tool: {tool}")
    return statistics.median(matches)


def quality_gate(metrics, floors):
    checks = {
        key: metrics[key] >= floors[key]
        for key in ("precision", "recall_lessons", "stable_tp")
    }
    return {"passed": all(checks.values()), "checks": checks}


def resource_gate(baseline, candidate, min_gain, max_regression):
    relative_delta = {
        key: (candidate[key] - baseline[key]) / baseline[key]
        for key in _RESOURCE_KEYS
    }
    improved = any(delta <= -min_gain for delta in relative_delta.values())
    bounded = all(delta <= max_regression for delta in relative_delta.values())
    return {
        "passed": improved and bounded,
        "improved": improved,
        "bounded": bounded,
        "relative_delta": relative_delta,
    }


def inherit_or_conservative_fp(candidate, baseline, tolerance):
    old = match_baseline(candidate, baseline, tolerance)
    if not old:
        return {
            **candidate,
            "verdict": "FP",
            "baseline_run_count": 0,
            "verdict_source": "screening-conservative",
        }
    inherited = {
        key: old.get(key)
        for key in (
            "verdict",
            "judge_cwe",
            "judge_reason",
            "judge_alias",
            "judge_confidence",
        )
    }
    return {
        **candidate,
        **inherited,
        "baseline_run_count": old.get("run_count", 0),
        "verdict_source": "baseline-independent",
    }


def prepare_novel_rows(candidates, baseline, tolerance):
    return [
        row
        for row in candidates
        if match_baseline(row, baseline, tolerance) is None
    ]


def merge_final_verdicts(candidates, baseline, novel_judged, tolerance):
    novel_by_key = {}
    for row in novel_judged:
        key = record_key(row)
        if key in novel_by_key:
            raise ValueError(f"duplicate novel verdict: {key}")
        if row.get("verdict") not in ("TP", "FP"):
            raise ValueError(f"invalid novel verdict: {key}")
        novel_by_key[key] = row

    merged = []
    used_novel = set()
    for candidate in candidates:
        old = match_baseline(candidate, baseline, tolerance)
        if old:
            inherited = {
                key: old.get(key)
                for key in (
                    "verdict",
                    "judge_cwe",
                    "judge_reason",
                    "judge_alias",
                    "judge_confidence",
                    "judge_reasoning_effort",
                    "judge_transport",
                )
            }
            result = {
                **candidate,
                **inherited,
                "baseline_run_count": old.get("run_count", 0),
                "verdict_source": "baseline-independent",
            }
        else:
            key = record_key(candidate)
            judged = novel_by_key.get(key)
            if not judged:
                raise ValueError(f"missing novel verdict: {key}")
            used_novel.add(key)
            result = {
                **candidate,
                **{
                    name: judged.get(name)
                    for name in (
                        "verdict",
                        "judge_cwe",
                        "judge_reason",
                        "judge_alias",
                        "judge_confidence",
                        "judge_reasoning_effort",
                        "judge_transport",
                    )
                },
                "verdict_source": "novel-independent",
            }
        if result.get("verdict") not in ("TP", "FP"):
            raise ValueError(f"candidate has no TP/FP verdict: {record_key(candidate)}")
        merged.append(result)

    unused = set(novel_by_key) - used_novel
    if unused:
        raise ValueError(f"unexpected novel verdict: {sorted(unused)[0]}")
    return merged


def count_expected_lesson_hits(tp_rows, expected_lessons):
    hits = set()
    for row in tp_rows:
        match = _LESSON.search(row["file"])
        if not match or match.group(1) not in expected_lessons:
            continue
        lesson = match.group(1)
        got = row.get("judge_cwe") or row.get("cwe")
        if got and got.upper() == expected_lessons[lesson]:
            hits.add(lesson)
    return len(hits)


def quality_metrics(judged, expected_lessons, stable_min, stable_field="run_count"):
    scoped = [row for row in judged if row.get("in_scope", True)]
    tp_rows = [row for row in scoped if row["verdict"] == "TP"]
    return {
        "judged": len(scoped),
        "tp": len(tp_rows),
        "fp": len(scoped) - len(tp_rows),
        "precision": len(tp_rows) / len(scoped) if scoped else 0.0,
        "recall_lessons": count_expected_lesson_hits(tp_rows, expected_lessons),
        "stable_tp": sum(
            row.get(stable_field, 0) >= stable_min for row in tp_rows
        ),
    }


def median_resources(run_resources):
    return {
        key: statistics.median(row[key] for row in run_resources)
        for key in _RESOURCE_KEYS
    }


def _load_sarif(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid SARIF: {path}") from exc


def load_profile_runs(profile_dir, calls):
    profile_dir = pathlib.Path(profile_dir)
    rows, resources = [], []
    if not profile_dir.exists():
        return rows, resources
    for tool_dir in sorted(path for path in profile_dir.iterdir() if path.is_dir()):
        for run_dir in sorted(
            path for path in tool_dir.iterdir() if path.is_dir()
        ):
            meta_path = run_dir / "run_meta.json"
            sarif_path = run_dir / "raw_output.sarif"
            if not meta_path.exists() or not sarif_path.exists():
                raise ValueError(f"missing run artifact: {run_dir}")
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid run manifest: {run_dir}") from exc
            if meta.get("valid") is not True:
                raise ValueError(f"invalid run manifest: {run_dir}")
            sarif = _load_sarif(sarif_path)
            cost = cost_for_run(calls, meta)
            if (
                not cost
                or cost["via_fallback_calls"]
                or cost["unknown_tool_calls"]
                or cost["llm_calls"] == 0
            ):
                raise ValueError(f"invalid proxy accounting: {run_dir}")
            resources.append(
                {
                    "tool": tool_dir.name,
                    "run": run_dir.name,
                    "phase": meta.get("phase"),
                    "wall_clock_s": meta["wall_clock_s"],
                    **cost,
                    "total_tokens": cost["input_tokens"]
                    + cost["output_tokens"],
                }
            )
            for sarif_run in sarif.get("runs", []):
                for result in sarif_run.get("results", []):
                    rows.append(
                        normalize_result(
                            result,
                            tool_dir.name,
                            run_dir.name,
                            meta.get("phase"),
                        )
                    )
    return rows, resources


def _calls(cfg):
    return read_jsonl(_ROOT / cfg["proxy"]["call_log"])


def _baseline_medians():
    rows = _baseline_cost_rows()
    grouped = collections.defaultdict(list)
    for row in rows:
        normalized = dict(row)
        normalized["total_tokens"] = (
            normalized["input_tokens"] + normalized["output_tokens"]
        )
        grouped[row["tool"]].append(normalized)
    return {tool: median_resources(items) for tool, items in grouped.items()}


def _baseline_cost_rows():
    return json.loads(_BASE_COST.read_text(encoding="utf-8"))


def _dedup(rows, cfg):
    tolerance = cfg["dedup"]["line_tolerance"]
    output = []
    for tool in sorted({row["tool"] for row in rows}):
        output.extend(
            dedup_tool([row for row in rows if row["tool"] == tool], tolerance)
        )
    return sorted(
        output,
        key=lambda row: (row["tool"], row["file"], row.get("line_min") or 0),
    )


def screening(profile):
    cfg = load_cfg()
    stage8 = cfg["stage8"]
    profile_root = _ROOT / "results" / "optimization" / profile
    stats_dir = profile_root / "stats"
    rows, resources = load_profile_runs(profile_root / "runs", _calls(cfg))
    if not rows or not resources:
        raise ValueError(f"profile has no valid runs: {profile}")

    deduped = _dedup(rows, cfg)
    baseline = read_jsonl(_BASE_JUDGED)
    tolerance = cfg["dedup"]["line_tolerance"]
    expected = {
        name: cwe
        for name, cwe in cfg["ground_truth"]["lessons"].items()
        if cwe
    }
    for row in deduped:
        row["in_scope"] = in_scope(row, cfg["judge"]["scope"])
    screened = [
        inherit_or_conservative_fp(row, baseline, tolerance) for row in deduped
    ]

    baseline_resource = _baseline_medians()
    per_tool = {}
    for tool in sorted({row["tool"] for row in screened}):
        tool_rows = [row for row in screened if row["tool"] == tool]
        tool_resources = [row for row in resources if row["tool"] == tool]
        measured = median_resources(tool_resources)
        quality = quality_metrics(
            tool_rows,
            expected,
            cfg["dedup"]["stable_min_runs"],
            stable_field="baseline_run_count",
        )
        q_gate = quality_gate(quality, stage8["quality_floor"][tool])
        r_gate = resource_gate(
            baseline_resource[tool],
            measured,
            stage8["min_resource_improvement"],
            stage8["max_resource_regression"],
        )
        per_tool[tool] = {
            "quality": quality,
            "quality_gate": q_gate,
            "resources": measured,
            "resource_gate": r_gate,
            "screening_passed": q_gate["passed"] and r_gate["passed"],
        }

    result = {
        "profile": profile,
        "mode": "screening",
        "actual_spend_usd": round(sum(r["cost_usd"] for r in resources), 6),
        "per_tool": per_tool,
    }
    write_jsonl(stats_dir / "findings.jsonl", rows)
    write_jsonl(stats_dir / "deduped.jsonl", deduped)
    write_jsonl(stats_dir / "screened.jsonl", screened)
    write_json(stats_dir / "cost_by_run.json", resources)
    write_json(stats_dir / "screening.json", result)
    return result


def _profile_data(profile):
    cfg = load_cfg()
    root = _ROOT / "results" / "optimization" / profile
    rows, resources = load_profile_runs(root / "runs", _calls(cfg))
    if not rows or not resources:
        raise ValueError(f"profile has no valid runs: {profile}")
    deduped = _dedup(rows, cfg)
    for row in deduped:
        row["in_scope"] = in_scope(row, cfg["judge"]["scope"])
    return cfg, root, rows, resources, deduped


def _screening_result(root):
    path = root / "stats" / "screening.json"
    if not path.exists():
        raise ValueError("screening result is missing")
    return json.loads(path.read_text(encoding="utf-8"))


def budget_check(profile, tool, next_run):
    cfg = load_cfg()
    stage8 = cfg["stage8"]
    if tool not in stage8["profiles"].get(profile, {}):
        raise ValueError(f"tool {tool} is not in Stage 8 profile {profile}")
    root = _ROOT / "results" / "optimization" / profile
    _, resources = load_profile_runs(root / "runs", _calls(cfg))
    actual = sum(row["cost_usd"] for row in resources)
    projected = projected_run_cost(_baseline_cost_rows(), tool, next_run)
    reserve = stage8["judge_reserve_usd"]
    cap = stage8["budget_usd"]
    result = {
        "profile": profile,
        "tool": tool,
        "next_run": next_run,
        "actual_spend_usd": round(actual, 6),
        "projected_run_usd": round(projected, 6),
        "judge_reserve_usd": reserve,
        "budget_usd": cap,
        "projected_total_usd": round(actual + projected + reserve, 6),
        "allowed": budget_allows(actual, projected, reserve, cap),
    }
    if not result["allowed"]:
        raise ValueError(
            f"budget cap would be exceeded: "
            f"${result['projected_total_usd']:.6f} > ${cap:.2f}"
        )
    return result


def passing_tools(profile, tool=None):
    root = _ROOT / "results" / "optimization" / profile
    screening_result = _screening_result(root)
    passed = [
        tool_id
        for tool_id, result in screening_result["per_tool"].items()
        if result["screening_passed"]
    ]
    if tool:
        if tool not in passed:
            raise ValueError(f"{tool} did not pass Stage 8 screening")
        return [tool]
    if not passed:
        raise ValueError("no tool passed Stage 8 screening")
    return sorted(passed)


def judge_spend(stats_dir, novel_rows):
    if not novel_rows:
        return 0.0
    state_path = pathlib.Path(stats_dir) / "judge-state.json"
    if not state_path.exists():
        raise ValueError("judge state is missing for novel findings")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if not state.get("collected"):
        raise ValueError("novel judge batch has not been collected")
    if not (state.get("usage") or {}).get("usage_complete"):
        raise ValueError("novel judge usage is incomplete")
    if "estimated_cost_usd" not in state:
        raise ValueError("novel judge cost is missing")
    return float(state["estimated_cost_usd"])


def prepare_novel_judge(profile):
    cfg, root, rows, resources, deduped = _profile_data(profile)
    screening_result = _screening_result(root)
    finalists = {
        tool
        for tool, result in screening_result["per_tool"].items()
        if result["screening_passed"]
    }
    if not finalists:
        raise ValueError("no tool passed screening")
    for tool in finalists:
        run_names = {row["run"] for row in resources if row["tool"] == tool}
        if len(run_names) < cfg["run"]["repeats"]:
            raise ValueError(
                f"finalist {tool} has {len(run_names)} runs; "
                f"expected {cfg['run']['repeats']}"
            )

    candidates = [
        row
        for row in deduped
        if row["tool"] in finalists and row.get("in_scope")
    ]
    baseline = read_jsonl(_BASE_JUDGED)
    novel = prepare_novel_rows(
        candidates, baseline, cfg["dedup"]["line_tolerance"]
    )
    stats_dir = root / "stats"
    write_jsonl(stats_dir / "findings.jsonl", rows)
    write_jsonl(stats_dir / "deduped.jsonl", deduped)
    write_json(stats_dir / "cost_by_run.json", resources)
    write_jsonl(stats_dir / "novel.jsonl", novel)
    result = {
        "profile": profile,
        "finalists": sorted(finalists),
        "candidate_count": len(candidates),
        "novel_count": len(novel),
        "novel_path": str((stats_dir / "novel.jsonl").relative_to(_ROOT)),
    }
    write_json(stats_dir / "novel-summary.json", result)
    return result


def final_evaluation(profile):
    cfg, root, rows, resources, deduped = _profile_data(profile)
    stage8 = cfg["stage8"]
    screening_result = _screening_result(root)
    stats_dir = root / "stats"
    baseline = read_jsonl(_BASE_JUDGED)
    novel_path = stats_dir / "judged-novel.jsonl"
    novel_judged = read_jsonl(novel_path)
    expected = {
        name: cwe
        for name, cwe in cfg["ground_truth"]["lessons"].items()
        if cwe
    }
    baseline_resource = _baseline_medians()
    final_judged = []
    per_tool = {}

    for tool, screened in sorted(screening_result["per_tool"].items()):
        if not screened["screening_passed"]:
            per_tool[tool] = {
                **screened,
                "pareto_passed": False,
                "decision_stage": "screening",
            }
            continue

        tool_resources = [row for row in resources if row["tool"] == tool]
        run_names = {row["run"] for row in tool_resources}
        if len(run_names) < cfg["run"]["repeats"]:
            raise ValueError(
                f"finalist {tool} has {len(run_names)} runs; "
                f"expected {cfg['run']['repeats']}"
            )
        candidates = [
            row
            for row in deduped
            if row["tool"] == tool and row.get("in_scope")
        ]
        judged = merge_final_verdicts(
            candidates,
            baseline,
            [row for row in novel_judged if row["tool"] == tool],
            cfg["dedup"]["line_tolerance"],
        )
        final_judged.extend(judged)
        quality = quality_metrics(
            judged, expected, cfg["dedup"]["stable_min_runs"]
        )
        measured = median_resources(tool_resources)
        q_gate = quality_gate(quality, stage8["quality_floor"][tool])
        r_gate = resource_gate(
            baseline_resource[tool],
            measured,
            stage8["min_resource_improvement"],
            stage8["max_resource_regression"],
        )
        per_tool[tool] = {
            "quality": quality,
            "quality_gate": q_gate,
            "resources": measured,
            "resource_gate": r_gate,
            "pareto_passed": q_gate["passed"] and r_gate["passed"],
            "decision_stage": "final",
        }

    run_spend = sum(row["cost_usd"] for row in resources)
    judge_spend_value = judge_spend(
        stats_dir, read_jsonl(stats_dir / "novel.jsonl")
    )
    actual_spend = run_spend + judge_spend_value
    result = {
        "profile": profile,
        "mode": "final",
        "run_spend_usd": round(run_spend, 6),
        "judge_spend_usd": round(judge_spend_value, 6),
        "actual_spend_usd": round(actual_spend, 6),
        "budget_usd": stage8["budget_usd"],
        "budget_passed": actual_spend <= stage8["budget_usd"],
        "per_tool": per_tool,
    }
    if not result["budget_passed"]:
        raise ValueError(
            f"Stage 8 spend ${actual_spend:.6f} exceeds "
            f"${stage8['budget_usd']:.2f}"
        )
    write_jsonl(stats_dir / "judged-final.jsonl", final_judged)
    write_json(stats_dir / "cost_by_run.json", resources)
    write_json(stats_dir / "final.json", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="balanced-v1")
    parser.add_argument("--mode", choices=("screening", "final"))
    parser.add_argument("--prepare-novel-judge", action="store_true")
    parser.add_argument("--budget-check", action="store_true")
    parser.add_argument("--require-screening-pass", action="store_true")
    parser.add_argument("--tool")
    parser.add_argument("--next-run", type=int)
    args = parser.parse_args()
    if args.budget_check:
        if not args.tool or not args.next_run:
            parser.error("--budget-check requires --tool and --next-run")
        result = budget_check(args.profile, args.tool, args.next_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.require_screening_pass:
        for tool in passing_tools(args.profile, args.tool):
            print(tool)
        return
    if args.prepare_novel_judge:
        result = prepare_novel_judge(args.profile)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.mode == "screening":
        result = screening(args.profile)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.mode == "final":
        result = final_evaluation(args.profile)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    parser.error("choose --mode screening|final or --prepare-novel-judge")


if __name__ == "__main__":
    main()
