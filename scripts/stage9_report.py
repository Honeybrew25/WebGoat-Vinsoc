#!/usr/bin/env python3
"""Generate and validate the deterministic Stage 9 benchmark report."""

import argparse
import hashlib
import json
import math
import os
import pathlib
import re
import statistics
import sys
from decimal import Decimal, InvalidOperation

import yaml


_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))

from stage7c_judge_agreement import agreement_metrics


_LESSON = re.compile(r"(?:^|/)lessons/([^/]+)/")


def approximate_f1(precision, hits, denominator):
    if (
        isinstance(precision, bool)
        or not isinstance(precision, (int, float))
        or not math.isfinite(precision)
        or precision < 0
        or precision > 1
    ):
        raise ValueError(f"precision: expected finite value from 0 to 1, got {precision!r}")
    if not isinstance(hits, int) or isinstance(hits, bool) or hits < 0:
        raise ValueError(f"hits: expected nonnegative integer, got {hits!r}")
    if not isinstance(denominator, int) or isinstance(denominator, bool):
        raise ValueError(f"denominator: expected integer, got {denominator!r}")
    if denominator <= 0:
        return 0.0
    recall = hits / denominator
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def lesson_hits(rows, expected):
    hits = set()
    for row in rows:
        if row.get("verdict") != "TP":
            continue
        match = _LESSON.search(row["file"].replace("\\", "/"))
        if not match:
            continue
        lesson = match.group(1)
        wanted = expected.get(lesson)
        got = row.get("judge_cwe") or row.get("cwe")
        if wanted and got and got.upper() == wanted.upper():
            hits.add(lesson)
    return hits


def common_blind_spots(rows, expected, tools):
    hit_union = set()
    for tool in tools:
        hit_union |= lesson_hits(
            [row for row in rows if row["tool"] == tool], expected
        )
    return [
        {"lesson": lesson, "expected_cwe": expected[lesson]}
        for lesson in sorted(expected)
        if lesson not in hit_union
    ]


def choose_recommendations(quality, resources, pareto):
    eligible = sorted(tool for tool, passed in pareto.items() if passed)
    if not eligible:
        raise ValueError("no Pareto-passing tool")

    deep = min(
        eligible,
        key=lambda tool: (
            -quality[tool]["recall_lessons"],
            -quality[tool]["tp"],
            -quality[tool]["approximate_f1"],
            tool,
        ),
    )
    ci = min(
        eligible,
        key=lambda tool: (
            -quality[tool]["precision"],
            resources[tool]["wall_clock_s"],
            resources[tool]["cost_usd"],
            tool,
        ),
    )
    if deep == ci:
        schedule = {"per_pr": ci, "nightly_or_release": ci}
    else:
        schedule = {"per_pr": ci, "nightly_or_release": deep}
    return {
        "deep_review": {
            "tool": deep,
            "reason_metrics": [
                "recall_lessons",
                "tp",
                "approximate_f1",
            ],
        },
        "ci_gate": {
            "tool": ci,
            "reason_metrics": [
                "precision",
                "wall_clock_s",
                "cost_usd",
            ],
        },
        "combined": {"schedule": schedule},
        "universal_winner": None,
    }


_RESOURCE_KEYS = ("wall_clock_s", "total_tokens", "cost_usd")


def _median_rows(rows, baseline):
    if not rows:
        raise ValueError("cannot calculate median for empty run rows")
    normalized = []
    for index, row in enumerate(rows, 1):
        value = dict(row)
        if baseline:
            for key in ("input_tokens", "output_tokens"):
                if key not in value:
                    raise ValueError(f"baseline median row {index} missing {key}")
                _assert_finite_nonnegative(
                    f"baseline median row {index}.{key}", value[key], True
                )
            value["total_tokens"] = (
                value["input_tokens"] + value["output_tokens"]
            )
        for key in _RESOURCE_KEYS:
            if key not in value:
                raise ValueError(f"median row {index} missing {key}")
            _assert_finite_nonnegative(
                f"median row {index}.{key}", value[key], key == "total_tokens"
            )
        normalized.append(value)
    return {
        key: statistics.median(row[key] for row in normalized)
        for key in _RESOURCE_KEYS
    }


def resource_summary(baseline_rows, optimized_rows, tools):
    result = {}
    for tool in tools:
        base = _median_rows(
            [row for row in baseline_rows if row["tool"] == tool],
            baseline=True,
        )
        optimized = _median_rows(
            [row for row in optimized_rows if row["tool"] == tool],
            baseline=False,
        )
        delta = {
            key: _relative_delta(tool, key, optimized[key], base[key])
            for key in _RESOURCE_KEYS
        }
        result[tool] = {
            "baseline_median": base,
            "optimized_median": optimized,
            "relative_delta": delta,
        }
    return result


def _relative_delta(tool, key, optimized, baseline):
    if baseline <= 0:
        raise ValueError(f"{tool} baseline {key}: expected positive value for delta")
    delta = (optimized - baseline) / baseline
    if not math.isfinite(delta):
        raise ValueError(f"{tool} relative delta {key}: non-finite derived value")
    return delta


def quality_summary(rows, expected, tools, stable_min):
    denominator = len(expected)
    result = {}
    for tool in tools:
        tool_rows = [row for row in rows if row["tool"] == tool]
        invalid = [
            row for row in tool_rows if row.get("verdict") not in ("TP", "FP")
        ]
        if invalid:
            raise ValueError(f"{tool}: final finding lacks TP/FP verdict")
        for row in tool_rows:
            _assert_finite_nonnegative(
                f"final {tool} run_count", row.get("run_count", 0), True
            )
        tp_rows = [row for row in tool_rows if row["verdict"] == "TP"]
        hits = lesson_hits(tp_rows, expected)
        precision = len(tp_rows) / len(tool_rows) if tool_rows else 0.0
        result[tool] = {
            "judged": len(tool_rows),
            "tp": len(tp_rows),
            "fp": len(tool_rows) - len(tp_rows),
            "precision": precision,
            "recall_lessons": len(hits),
            "recall_denominator": denominator,
            "recall_fraction": len(hits) / denominator if denominator else 0.0,
            "stable_tp": sum(
                row.get("run_count", 0) >= stable_min for row in tp_rows
            ),
            "approximate_f1": approximate_f1(
                precision, len(hits), denominator
            ),
            "lessons_hit": sorted(hits),
        }
    return result


def sha256_file(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"


def _money_sum(values):
    total = Decimal("0")
    for index, value in enumerate(values, 1):
        _assert_finite_nonnegative(f"money value {index}", value)
        try:
            total += Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"money value {index}: invalid decimal {value!r}") from exc
    return float(total)


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON artifact: {path}") from exc


def _read_jsonl(path):
    rows = []
    try:
        for line_no, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSONL artifact: {path}:{line_no}"
                ) from exc
    except OSError as exc:
        raise ValueError(f"cannot read artifact: {path}") from exc
    return rows


def source_paths(root, profile):
    rel = {
        "config": "config/benchmark.yaml",
        "baseline_cost": "results/stats/cost_by_run.json",
        "counts": "results/stats/counts.json",
        "precision_same": "results/stats/precision.json",
        "precision_independent": "results/stats/precision-independent.json",
        "recall_baseline": "results/stats/recall.json",
        "agreement": "results/stats/judge_agreement.json",
        "judged_same": "results/findings/normalized/judged.jsonl",
        "judged_independent": (
            "results/findings/normalized/judged-independent.jsonl"
        ),
        "profile": f"results/optimization/{profile}/profile.json",
        "optimized_cost": (
            f"results/optimization/{profile}/stats/cost_by_run.json"
        ),
        "final": f"results/optimization/{profile}/stats/final.json",
        "judged_final": (
            f"results/optimization/{profile}/stats/judged-final.jsonl"
        ),
        "judged_novel": (
            f"results/optimization/{profile}/stats/judged-novel.jsonl"
        ),
        "judge_state": (
            f"results/optimization/{profile}/stats/judge-state.json"
        ),
    }
    paths = {name: root / value for name, value in rel.items()}
    for name, path in paths.items():
        resolved = path.resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError(f"source path escapes repository: {name}") from exc
        if not path.is_file():
            raise ValueError(f"missing source artifact: {path}")
    return paths


def load_sources(root):
    config_path = root / "config" / "benchmark.yaml"
    try:
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"invalid YAML artifact: {config_path}") from exc
    profile = cfg["stage8"]["active_profile"]
    paths = source_paths(root, profile)
    jsonl_names = {
        "judged_same",
        "judged_independent",
        "judged_final",
        "judged_novel",
    }
    values = {"config": cfg, "paths": paths, "root": root}
    for name, path in paths.items():
        if name == "config":
            continue
        values[name] = (
            _read_jsonl(path) if name in jsonl_names else _read_json(path)
        )
    return values


def _assert_equal(label, actual, expected):
    if actual != expected:
        raise ValueError(f"{label}: expected {expected!r}, got {actual!r}")


def _assert_close(label, actual, expected, tolerance=1e-9):
    try:
        actual_number = float(actual)
        expected_number = float(expected)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{label}: expected finite numeric values, got "
            f"{actual!r} and {expected!r}"
        ) from exc
    if not math.isfinite(actual_number) or not math.isfinite(expected_number):
        raise ValueError(
            f"{label}: non-finite comparison values "
            f"{actual!r} and {expected!r}"
        )
    if abs(actual_number - expected_number) > tolerance:
        raise ValueError(f"{label}: expected {expected!r}, got {actual!r}")


def _assert_finite_nonnegative(label, value, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}: expected finite nonnegative number, got {value!r}")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{label}: expected finite nonnegative number, got {value!r}")
    if integer and int(value) != value:
        raise ValueError(f"{label}: expected nonnegative integer, got {value!r}")


def _expected_runs(repeats, separate_cold_warm):
    if repeats != 3 or isinstance(repeats, bool):
        raise ValueError(f"run repeats: expected exactly three, got {repeats!r}")
    if separate_cold_warm is not True:
        raise ValueError("run configuration must require distinct cold and warm runs")
    return [
        ("run-01-cold", "cold"),
        ("run-02-warm", "warm"),
        ("run-03-warm", "warm"),
    ]


_RUN_NUMERIC_FIELDS = (
    ("wall_clock_s", False),
    ("llm_calls", True),
    ("input_tokens", True),
    ("output_tokens", True),
    ("cost_usd", False),
    ("via_fallback_calls", True),
    ("unknown_tool_calls", True),
)


def _validate_run_rows(label, rows, tools, repeats, separate_cold_warm, target_sha):
    if not isinstance(rows, list):
        raise ValueError(f"{label} run artifact: expected list")
    expected_runs = dict(_expected_runs(repeats, separate_cold_warm))
    _assert_equal(f"{label} tools", {row.get("tool") for row in rows}, set(tools))
    _assert_equal(f"{label} total run count", len(rows), len(tools) * repeats)
    for tool in tools:
        tool_rows = [row for row in rows if row.get("tool") == tool]
        _assert_equal(f"{label} run count for {tool}", len(tool_rows), repeats)
        for index, row in enumerate(tool_rows, 1):
            for field in ("tool", "run", "phase", *_RUN_NUMERIC_FIELDS):
                field_name = field[0] if isinstance(field, tuple) else field
                if field_name not in row:
                    raise ValueError(f"{label} {tool} row {index} missing {field_name}")
        run_ids = [row["run"] for row in tool_rows]
        _assert_equal(f"{label} run IDs for {tool}", set(run_ids), set(expected_runs))
        _assert_equal(
            f"{label} unique run IDs for {tool}", len(run_ids), len(set(run_ids))
        )
        for row in tool_rows:
            run = row["run"]
            _assert_equal(
                f"{label} phase for {tool} {run}", row["phase"], expected_runs[run]
            )
            for field, integer in _RUN_NUMERIC_FIELDS:
                _assert_finite_nonnegative(f"{label} {tool} {run}.{field}", row[field], integer)
            _assert_equal(f"{label} fallback calls for {tool} {run}", row["via_fallback_calls"], 0)
            _assert_equal(f"{label} unknown calls for {tool} {run}", row["unknown_tool_calls"], 0)
            if label == "baseline":
                if "target_sha" not in row:
                    raise ValueError(f"baseline {tool} {run} missing target_sha")
                _assert_equal(
                    f"baseline target SHA for {tool} {run}", row["target_sha"], target_sha
                )
            else:
                if "total_tokens" not in row:
                    raise ValueError(f"optimized {tool} {run} missing total_tokens")
                _assert_finite_nonnegative(
                    f"optimized {tool} {run}.total_tokens", row["total_tokens"], True
                )
                _assert_equal(
                    f"optimized total tokens for {tool} {run}",
                    row["total_tokens"],
                    row["input_tokens"] + row["output_tokens"],
                )


def _baseline_loose_lesson_hits(rows, expected):
    hits = set()
    for row in rows:
        if row.get("verdict") != "TP":
            continue
        match = _LESSON.search(row["file"].replace("\\", "/"))
        if match and match.group(1) in expected:
            hits.add(match.group(1))
    return hits


def _record_key(row):
    return (
        row["tool"],
        row["file"],
        row.get("line_min"),
        row["title"],
    )


def _aligned_verdict_map(label, rows, tools):
    _assert_equal(f"{label} raw verdict count", len(rows), 238)
    _assert_equal(f"{label} tools", {row.get("tool") for row in rows}, set(tools))
    result = {}
    for index, row in enumerate(rows, 1):
        verdict = row.get("verdict")
        if verdict not in {"TP", "FP"}:
            raise ValueError(f"{label} verdict at row {index}: expected TP/FP, got {verdict!r}")
        try:
            key = _record_key(row)
        except KeyError as exc:
            raise ValueError(f"{label} row {index} missing alignment field {exc.args[0]}") from exc
        if key in result:
            raise ValueError(f"duplicate {label} alignment key: {key!r}")
        result[key] = row
    return result


def _precision_by_tool(label, rows, tools, artifact):
    _assert_equal(f"{label} precision tools", set(artifact["per_tool"]), set(tools))
    result = {}
    for tool in tools:
        tool_rows = [row for row in rows if row["tool"] == tool]
        tp = sum(row["verdict"] == "TP" for row in tool_rows)
        precision = tp / len(tool_rows)
        stored = artifact["per_tool"][tool]
        _assert_equal(f"{label} precision judged {tool}", stored["judged"], len(tool_rows))
        _assert_equal(f"{label} precision TP {tool}", stored["tp"], tp)
        _assert_equal(f"{label} precision FP {tool}", stored["fp"], len(tool_rows) - tp)
        _assert_equal(f"{label} precision {tool}", stored["precision"], round(precision, 4))
        result[tool] = round(precision, 4)
    return result


def _validate_recall(sources, expected, tools):
    recall = sources["recall_baseline"]
    configured_granularity = sources["config"]["ground_truth"].get("granularity")
    _assert_equal("recall granularity config", configured_granularity, "lesson")
    _assert_equal("recall granularity", recall.get("granularity"), configured_granularity)
    _assert_equal("recall denominator", recall.get("denominator"), len(expected))
    _assert_equal("recall tools", set(recall.get("per_tool", {})), set(tools))
    all_strict = set()
    for tool in tools:
        rows = [row for row in sources["judged_same"] if row["tool"] == tool]
        strict = sorted(lesson_hits(rows, expected))
        loose = sorted(_baseline_loose_lesson_hits(rows, expected))
        stored = recall["per_tool"][tool]
        _assert_equal(f"recall per-tool denominator {tool}", stored.get("denominator"), len(expected))
        _assert_equal(f"recall strict lessons {tool}", stored.get("lessons_hit_cwe"), strict)
        _assert_equal(f"recall loose lessons {tool}", stored.get("lessons_hit_any"), loose)
        _assert_equal(f"recall strict count {tool}", stored.get("recall_strict"), len(strict))
        _assert_equal(f"recall loose count {tool}", stored.get("recall_loose"), len(loose))
        all_strict.update(strict)
    _assert_equal(
        "recall blind spots",
        recall.get("blind_spots"),
        sorted(set(expected) - all_strict),
    )


def _validate_final_gates(tool, stored, measured, relative_delta, cfg):
    quality_gate = stored["quality_gate"]
    quality_floor = cfg["stage8"]["quality_floor"][tool]
    expected_quality_checks = {
        key: measured[key] >= quality_floor[key]
        for key in ("precision", "recall_lessons", "stable_tp")
    }
    _assert_equal(
        f"{tool} quality gate check keys",
        set(quality_gate["checks"]),
        set(expected_quality_checks),
    )
    _assert_equal(
        f"{tool} quality gate checks",
        quality_gate["checks"],
        expected_quality_checks,
    )
    expected_quality_passed = all(expected_quality_checks.values())
    _assert_equal(
        f"{tool} quality gate passed", quality_gate["passed"], expected_quality_passed
    )

    resource_gate = stored["resource_gate"]
    _assert_equal(
        f"{tool} resource gate delta keys",
        set(resource_gate["relative_delta"]),
        set(_RESOURCE_KEYS),
    )
    for key in _RESOURCE_KEYS:
        _assert_close(
            f"{tool} resource gate delta {key}",
            resource_gate["relative_delta"][key],
            relative_delta[key],
        )
    expected_improved = any(
        delta <= -cfg["stage8"]["min_resource_improvement"]
        for delta in relative_delta.values()
    )
    expected_bounded = all(
        delta <= cfg["stage8"]["max_resource_regression"]
        for delta in relative_delta.values()
    )
    _assert_equal(
        f"{tool} resource gate improved", resource_gate["improved"], expected_improved
    )
    _assert_equal(
        f"{tool} resource gate bounded", resource_gate["bounded"], expected_bounded
    )
    expected_resource_passed = expected_improved and expected_bounded
    _assert_equal(
        f"{tool} resource gate passed", resource_gate["passed"], expected_resource_passed
    )
    expected_pareto_passed = expected_quality_passed and expected_resource_passed
    if stored["pareto_passed"] != expected_pareto_passed:
        raise ValueError(f"{tool}: finalist did not pass every Pareto gate")
    _assert_equal(f"{tool} final decision stage", stored["decision_stage"], "final")
    return expected_pareto_passed


def _validate_agreement(sources, tools, scope):
    weak = _aligned_verdict_map("same-model", sources["judged_same"], tools)
    strong = _aligned_verdict_map("independent", sources["judged_independent"], tools)
    _assert_equal("aligned baseline verdict keys", set(weak), set(strong))
    ordered = sorted(weak)
    metrics = agreement_metrics(
        [weak[key]["verdict"] for key in ordered],
        [strong[key]["verdict"] for key in ordered],
    )
    stored = sources["agreement"]
    _assert_equal("agreement n", stored["n"], metrics["n"])
    _assert_equal(
        "agreement rounded",
        stored["agreement"],
        round(metrics["agreement"], 4),
    )
    _assert_equal(
        "kappa rounded",
        stored["cohens_kappa"],
        round(metrics["cohens_kappa"], 4),
    )
    weak_precision = _precision_by_tool(
        "same-model", sources["judged_same"], tools, sources["precision_same"]
    )
    independent_precision = _precision_by_tool(
        "independent", sources["judged_independent"], tools, sources["precision_independent"]
    )
    _assert_equal("agreement precision tools", set(stored["precision_by_tool"]), set(tools))
    for tool in tools:
        _assert_equal(
            f"agreement same-model precision {tool}",
            stored["precision_by_tool"][tool]["weak"],
            weak_precision[tool],
        )
        _assert_equal(
            f"agreement independent precision {tool}",
            stored["precision_by_tool"][tool]["independent"],
            independent_precision[tool],
        )
    return {
        "n": metrics["n"],
        "agreement": metrics["agreement"],
        "cohens_kappa": metrics["cohens_kappa"],
        "baseline_precision_by_tool": {
            tool: {"weak": weak_precision[tool], "independent": independent_precision[tool]}
            for tool in tools
        },
        "scope": "Stage 7 baseline aligned verdict set",
    }


def build_summary_from_sources(sources):
    cfg = sources["config"]
    profile_name = cfg["stage8"]["active_profile"]
    profile = sources["profile"]
    final = sources["final"]
    tools = sorted(cfg["stage8"]["profiles"][profile_name])
    repeats = cfg["run"]["repeats"]
    enabled_tools = {
        item["id"] for item in cfg["tools"] if item.get("enabled") is True
    }
    expected = {
        lesson: cwe
        for lesson, cwe in cfg["ground_truth"]["lessons"].items()
        if cwe
    }

    _assert_equal("profile name", profile["profile"], profile_name)
    _assert_equal("final profile", final["profile"], profile_name)
    _assert_equal("profile target SHA", profile["target_sha"], cfg["target"]["sha"])
    _assert_equal("profile model", profile["model"], cfg["model"])
    _assert_equal("profile budget", profile["budget_usd"], cfg["stage8"]["budget_usd"])
    _assert_equal("active profile tools", set(tools), enabled_tools)
    _assert_equal("quality floor tools", set(cfg["stage8"]["quality_floor"]), set(tools))
    _assert_equal(
        "profile knobs", profile["knobs"], cfg["stage8"]["profiles"][profile_name]
    )
    _assert_equal("final tools", set(final["per_tool"]), set(tools))
    _assert_close("final budget", final["budget_usd"], cfg["stage8"]["budget_usd"])
    _assert_close("final budget profile linkage", final["budget_usd"], profile["budget_usd"])

    _validate_run_rows(
        "baseline",
        sources["baseline_cost"],
        tools,
        repeats,
        cfg["run"]["separate_cold_warm"],
        cfg["target"]["sha"],
    )
    _validate_run_rows(
        "optimized",
        sources["optimized_cost"],
        tools,
        repeats,
        cfg["run"]["separate_cold_warm"],
        cfg["target"]["sha"],
    )

    counts = sources["counts"]
    _assert_equal("Stage 6 count tools", set(counts["per_tool"]), set(tools))
    for tool in tools:
        row = counts["per_tool"].get(tool)
        if not row:
            raise ValueError(f"Stage 6 count row missing for {tool}")
        if row["stable"] > row["unique"]:
            raise ValueError(f"Stage 6 stable count exceeds unique for {tool}")

    if any(
        row.get("verdict_source")
        not in {"baseline-independent", "novel-independent"}
        for row in sources["judged_final"]
    ):
        raise ValueError("invalid final verdict provenance")
    _assert_equal(
        "final verdict tools",
        {row.get("tool") for row in sources["judged_final"]},
        set(tools),
    )

    quality = quality_summary(
        sources["judged_final"],
        expected,
        tools,
        cfg["dedup"]["stable_min_runs"],
    )
    resources = resource_summary(
        sources["baseline_cost"], sources["optimized_cost"], tools
    )
    pareto = {}
    for tool in tools:
        stored = final["per_tool"][tool]
        measured = quality[tool]
        for field in (
            "judged",
            "tp",
            "fp",
            "recall_lessons",
            "stable_tp",
        ):
            _assert_equal(
                f"final {tool}.{field}",
                stored["quality"][field],
                measured[field],
            )
        _assert_close(
            f"final {tool}.precision",
            stored["quality"]["precision"],
            measured["precision"],
        )
        for key in _RESOURCE_KEYS:
            _assert_close(
                f"final {tool}.resources.{key}",
                stored["resources"][key],
                resources[tool]["optimized_median"][key],
            )
            _assert_close(
                f"final {tool}.relative_delta.{key}",
                stored["resource_gate"]["relative_delta"][key],
                resources[tool]["relative_delta"][key],
            )
        gates_passed = _validate_final_gates(
            tool,
            stored,
            measured,
            resources[tool]["relative_delta"],
            cfg,
        )
        if not gates_passed:
            raise ValueError(f"{tool}: finalist did not pass every Pareto gate")
        pareto[tool] = True

    state = sources["judge_state"]
    novel = sources["judged_novel"]
    _assert_equal("novel batch collected", state.get("collected"), True)
    _assert_equal("novel verdict count", len(novel), state["collected_count"])
    _assert_equal(
        "novel successful count",
        int(state["batch_stats"]["successfulRequestCount"]),
        len(novel),
    )
    _assert_equal("novel usage complete", state["usage"]["usage_complete"], True)
    if state.get("item_errors"):
        raise ValueError("novel judge contains item errors")

    _assert_finite_nonnegative("judge estimated cost", state["estimated_cost_usd"])
    _assert_finite_nonnegative("judge usage estimated cost", state["usage"]["estimated_cost_usd"])
    _assert_close("judge estimated cost linkage", state["estimated_cost_usd"], state["usage"]["estimated_cost_usd"])
    run_spend = _money_sum(row["cost_usd"] for row in sources["optimized_cost"])
    judge_spend = float(state["estimated_cost_usd"])
    actual_spend = _money_sum((run_spend, judge_spend))
    _assert_close("final run spend", final["run_spend_usd"], run_spend)
    _assert_close("final judge spend", final["judge_spend_usd"], judge_spend)
    _assert_close("final actual spend", final["actual_spend_usd"], actual_spend)
    expected_budget_passed = actual_spend <= final["budget_usd"]
    _assert_equal("final budget passed", final["budget_passed"], expected_budget_passed)
    if not expected_budget_passed:
        raise ValueError(
            f"Stage 8 spend {actual_spend} exceeds cap "
            f"{final['budget_usd']}"
        )

    sensitivity = _validate_agreement(sources, tools, cfg["judge"]["scope"])
    same_precision = sources["precision_same"]
    independent_precision = sources["precision_independent"]
    _assert_equal("same judge scope", same_precision["scope"], cfg["judge"]["scope"])
    _assert_equal(
        "independent judge scope",
        independent_precision["scope"],
        cfg["judge"]["scope"],
    )
    _validate_recall(sources, expected, tools)

    recommendations = choose_recommendations(
        quality,
        {tool: resources[tool]["optimized_median"] for tool in tools},
        pareto,
    )
    provenance = [
        {
            "path": path.relative_to(sources["root"]).as_posix(),
            "sha256": sha256_file(path),
        }
        for path in sorted(
            sources["paths"].values(),
            key=lambda item: item.relative_to(sources["root"]).as_posix(),
        )
    ]
    limitations = [
        "Recall và F1 là xấp xỉ ở mức lesson, không phải mức dòng.",
        "Cohen's kappa được đo trên baseline Stage 7, không phải finding mới Stage 8.",
        "WebGoat cố tình chứa lỗ hổng và không đại diện cho mọi codebase production.",
        "Precision phụ thuộc lựa chọn judge; báo cáo giữ cả hai bộ baseline.",
    ]
    return {
        "schema_version": 1,
        "benchmark": {
            "target_name": cfg["target"]["name"],
            "target_sha": cfg["target"]["sha"],
            "target_ref": cfg["target"]["ref"],
            "language": cfg["target"]["language"],
            "model": cfg["model"],
            "scope": cfg["judge"]["scope"],
            "repeats": repeats,
            "active_profile": profile_name,
        },
        "findings": {
            "per_tool": counts["per_tool"],
            "cross_tool_matches": counts["cross_tool_matches"],
        },
        "quality": quality,
        "judge_sensitivity": sensitivity,
        "resources": {
            "run_count": len(sources["optimized_cost"]),
            "per_tool": resources,
        },
        "budget": {
            "run_spend_usd": run_spend,
            "judge_spend_usd": judge_spend,
            "actual_spend_usd": actual_spend,
            "cap_usd": cfg["stage8"]["budget_usd"],
            "passed": True,
        },
        "blind_spots": common_blind_spots(
            sources["judged_final"], expected, tools
        ),
        "recommendations": recommendations,
        "limitations": limitations,
        "provenance": provenance,
    }


def build_summary(root):
    return build_summary_from_sources(load_sources(pathlib.Path(root)))


def _pct(value, digits=1):
    return f"{value * 100:.{digits}f}%"


def _money(value):
    return f"${value:.6f}"


def render_markdown(summary):
    benchmark = summary["benchmark"]
    quality = summary["quality"]
    resources = summary["resources"]["per_tool"]
    sensitivity = summary["judge_sensitivity"]
    budget = summary["budget"]
    recommendations = summary["recommendations"]
    deep = recommendations["deep_review"]["tool"]
    ci = recommendations["ci_gate"]["tool"]
    same_winner = deep == ci
    conclusion = (
        f"`{deep}` thắng cả review chuyên sâu và CI theo các tiêu chí đo được; "
        "không chọn scanner thứ hai."
        if same_winner
        else "Không có người thắng tuyệt đối theo cả hai tình huống; "
        f"`{deep}` phù hợp review chuyên sâu; `{ci}` phù hợp CI trên mỗi PR."
    )
    ci_card = (
        f"> **CI gate:** dùng `{ci}` trên mỗi PR; không chọn scanner thứ hai."
        if same_winner
        else f"> **CI gate:** dùng `{ci}` trên mỗi PR; chạy `{deep}` nightly hoặc trước release."
    )
    lines = [
        "# Giai đoạn 9 — Báo cáo cuối",
        "",
        f"> ✅ **KẾT LUẬN:** {conclusion}",
        "",
        f"> **Review chuyên sâu:** dùng `{deep}` để ưu tiên độ bao phủ, "
        "sau đó review thủ công các finding.",
        "",
        ci_card,
        "",
        "## 1. Tóm tắt điều hành",
        "",
        f"Benchmark cố định `{benchmark['model']['id']}` trên "
        f"{benchmark['target_name']} `{benchmark['target_sha'][:12]}` và "
        f"dùng profile `{benchmark['active_profile']}`.",
        "",
        "| Tool | TP / FP | Precision độc lập | Recall lesson | Stable TP | F1 xấp xỉ |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for tool in sorted(quality):
        row = quality[tool]
        lines.append(
            f"| {tool} | {row['tp']} / {row['fp']} | "
            f"{_pct(row['precision'], 2)} | "
            f"{row['recall_lessons']}/{row['recall_denominator']} | "
            f"{row['stable_tp']} | {row['approximate_f1']:.3f} |"
        )
    lines.extend([
        "",
        "| Tool | Wall clock baseline → tối ưu | Δ | "
        "Token baseline → tối ưu | Δ | Cost baseline → tối ưu | Δ |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for tool in sorted(resources):
        row = resources[tool]
        baseline = row["baseline_median"]
        optimized = row["optimized_median"]
        delta = row["relative_delta"]
        lines.append(
            f"| {tool} | {baseline['wall_clock_s']:.0f}s → "
            f"{optimized['wall_clock_s']:.0f}s | "
            f"{_pct(delta['wall_clock_s'])} | "
            f"{baseline['total_tokens']:,.0f} → "
            f"{optimized['total_tokens']:,.0f} | "
            f"{_pct(delta['total_tokens'])} | "
            f"{_money(baseline['cost_usd'])} → "
            f"{_money(optimized['cost_usd'])} | "
            f"{_pct(delta['cost_usd'])} |"
        )
    lines.extend([
        "",
        f"Tổng chi Stage 8: **{_money(budget['actual_spend_usd'])}"
        f"/{_money(budget['cap_usd'])}**.",
        "",
        "## 2. Benchmark identity và fairness",
        "",
        f"- Target: {benchmark['target_name']} `{benchmark['target_ref']}` "
        f"tại `{benchmark['target_sha']}`.",
        f"- Model: `{benchmark['model']['id']}`, temperature "
        f"`{benchmark['model']['temperature']}`, top_p "
        f"`{benchmark['model']['top_p']}`.",
        f"- Phạm vi: `{benchmark['language']}`, loại test và vendor theo "
        "`config/benchmark.yaml`.",
        f"- Mỗi tool chạy {benchmark['repeats']} lần; báo cáo resource dùng median.",
        "",
        "## 3. Chất lượng cuối và F1 xấp xỉ",
        "",
        "Precision dùng judge độc lập. Recall và F1 chỉ là xấp xỉ mức lesson, "
        "không phải mức dòng.",
        "",
    ])
    for tool in sorted(quality):
        row = quality[tool]
        lines.append(
            f"- `{tool}`: precision {_pct(row['precision'], 2)}, "
            f"recall {row['recall_lessons']}/{row['recall_denominator']}, "
            f"F1 xấp xỉ {row['approximate_f1']:.3f}."
        )
    lines.extend([
        "",
        "## 4. Độ nhạy theo judge",
        "",
        f"Trên {sensitivity['scope']}, agreement là "
        f"{_pct(sensitivity['agreement'])} và Cohen's kappa là "
        f"{sensitivity['cohens_kappa']:.3f}. Đây là mức phụ thuộc judge đáng kể; "
        "không coi một precision là ground truth tuyệt đối.",
        "",
        "| Tool | Precision cùng model | Precision độc lập | Chênh |",
        "|---|---:|---:|---:|",
    ])
    for tool, row in sorted(
        sensitivity["baseline_precision_by_tool"].items()
    ):
        lines.append(
            f"| {tool} | {_pct(row['weak'])} | "
            f"{_pct(row['independent'])} | "
            f"{_pct(row['independent'] - row['weak'])} |"
        )
    lines.extend([
        "",
        "## 5. Điểm mù chung cuối",
        "",
    ])
    for item in summary["blind_spots"]:
        lines.append(f"- `{item['lesson']}` — `{item['expected_cwe']}`")
    deployment = [
        "",
        "## 6. Khuyến nghị triển khai",
        "",
        f"- Mỗi PR: `{ci}`.",
    ]
    if same_winner:
        deployment.extend([
            f"- Review chuyên sâu: `{deep}`.",
            f"- Chỉ `{deep}` được chọn cho cả hai lịch; không chọn scanner thứ hai theo tiêu chí đo được.",
        ])
    else:
        deployment.extend([
            f"- Nightly hoặc trước release: `{deep}`.",
            f"- Hai công cụ phục vụ hai lịch khác nhau; `{ci}` cho CI và `{deep}` cho coverage review.",
        ])
    deployment.extend([
        "- Các finding cần review thủ công; không dùng count thô để block merge.",
        "- Báo cáo không tạo weighted score.",
        "",
        "## 7. Giới hạn phương pháp",
        "",
    ])
    lines.extend(deployment)
    lines.extend(f"- {item}" for item in summary["limitations"])
    lines.extend([
        "",
        "## 8. Tái lập báo cáo",
        "",
        "```bash",
        "uv run --with pyyaml python scripts/stage9_report.py --write",
        "uv run --with pyyaml python scripts/stage9_report.py --check",
        "```",
        "",
        "## 9. Provenance",
        "",
        "| Artifact | SHA-256 |",
        "|---|---|",
    ])
    for item in summary["provenance"]:
        lines.append(f"| `{item['path']}` | `{item['sha256']}` |")
    return "\n".join(lines) + "\n"


_SUMMARY_REL = pathlib.Path("results/report/final-summary.json")
_REPORT_REL = pathlib.Path("docs/stage9-bao-cao-cuoi.md")


def _atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def write_outputs(root, summary):
    json_text = canonical_json(summary)
    markdown = render_markdown(summary)
    _atomic_write(pathlib.Path(root) / _SUMMARY_REL, json_text)
    _atomic_write(pathlib.Path(root) / _REPORT_REL, markdown)


def check_outputs(root, summary):
    expected = {
        _SUMMARY_REL: canonical_json(summary),
        _REPORT_REL: render_markdown(summary),
    }
    for relative, content in expected.items():
        path = pathlib.Path(root) / relative
        if not path.exists() or path.read_bytes() != content.encode("utf-8"):
            raise ValueError(f"stale generated output: {relative.as_posix()}")


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    summary = build_summary(_ROOT)
    if args.write:
        write_outputs(_ROOT, summary)
        print(f"WROTE {_SUMMARY_REL.as_posix()}")
        print(f"WROTE {_REPORT_REL.as_posix()}")
    else:
        check_outputs(_ROOT, summary)
        print("STAGE9 REPORT OK")


if __name__ == "__main__":
    main()
