# Stage 9 Final Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic Stage 9 generator that validates all completed benchmark artifacts and emits a machine-readable final summary plus a two-layer Vietnamese decision report.

**Architecture:** A standalone Python script loads repository-confined Stage 4–8 YAML/JSON/JSONL artifacts, recomputes derived metrics, validates cross-artifact invariants, and builds one versioned summary dictionary. Canonical JSON and Markdown renderers consume only that dictionary; `--write` uses atomic replacements and `--check` detects stale outputs without writing.

**Tech Stack:** Python 3 standard library, PyYAML through `uv`, `unittest`, JSON/JSONL, Markdown.

## Global Constraints

- Do not call an API, scanner, proxy, or judge.
- Do not mutate any Stage 4–8 artifact.
- Read the active Stage 8 profile from `config/benchmark.yaml`; do not hard-code `balanced-v1` in report logic.
- Primary quality uses final Stage 8 independent verdicts.
- Judge sensitivity uses aligned Stage 7 baseline same-model and independent verdicts.
- Recall and F1 must be labeled approximate lesson-level metrics.
- Emit scenario recommendations only: Metis-style coverage review and SAIST-style CI gate; no weighted score and no universal winner.
- JSON must use schema version 1, sorted keys, UTF-8, LF endings, two-space indentation, and one trailing newline.
- Output must contain no dynamic timestamp, hostname, absolute path, secret, or API key.
- Every source artifact must have a repository-relative path and SHA-256 digest in provenance.
- Exact Stage 8 floating fields use tolerance `1e-9`; rounded Stage 7 metrics compare with `round(value, 4)`.
- Preserve the current dirty/staged worktree. Do not commit, push, reset, clean, stage files, or alter the existing index.
- Use Git Bash explicitly on this Windows host for shell syntax checks.

---

### Task 1: Add pure quality, blind-spot, and recommendation functions

**Files:**
- Create: `scripts/stage9_report.py`
- Create: `tests/test_stage9_report.py`

**Interfaces:**
- Consumes: normalized finding dictionaries and `ground_truth.lessons`.
- Produces:
  - `approximate_f1(precision: float, hits: int, denominator: int) -> float`
  - `lesson_hits(rows: list[dict], expected: dict[str, str]) -> set[str]`
  - `common_blind_spots(rows: list[dict], expected: dict[str, str], tools: list[str]) -> list[dict]`
  - `choose_recommendations(quality: dict, resources: dict, pareto: dict) -> dict`

- [ ] **Step 1: Write failing tests for formula and strict lesson hits**

Create `tests/test_stage9_report.py`:

```python
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "stage9_report", ROOT / "scripts" / "stage9_report.py"
)
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


def finding(tool, lesson, cwe, verdict="TP", run_count=3):
    return {
        "tool": tool,
        "file": f"src/main/java/org/owasp/webgoat/lessons/{lesson}/A.java",
        "verdict": verdict,
        "judge_cwe": cwe,
        "cwe": cwe,
        "run_count": run_count,
    }


class Stage9PureMetricTests(unittest.TestCase):
    def test_approximate_f1_uses_lesson_recall(self):
        result = REPORT.approximate_f1(0.75, 3, 4)
        self.assertAlmostEqual(0.75, result)
        self.assertEqual(0.0, REPORT.approximate_f1(0.0, 0, 4))
        self.assertEqual(0.0, REPORT.approximate_f1(0.5, 0, 0))

    def test_lesson_hits_require_tp_and_expected_cwe(self):
        expected = {"sqlinjection": "CWE-89", "ssrf": "CWE-918"}
        rows = [
            finding("arm-metis", "sqlinjection", "CWE-89"),
            finding("arm-metis", "ssrf", "CWE-79"),
            finding("arm-metis", "ssrf", "CWE-918", verdict="FP"),
        ]
        self.assertEqual({"sqlinjection"}, REPORT.lesson_hits(rows, expected))

    def test_common_blind_spots_use_final_verdicts(self):
        expected = {"sqlinjection": "CWE-89", "ssrf": "CWE-918"}
        rows = [
            finding("arm-metis", "sqlinjection", "CWE-89"),
            finding("datadog-saist", "sqlinjection", "CWE-89"),
        ]
        self.assertEqual(
            [{"lesson": "ssrf", "expected_cwe": "CWE-918"}],
            REPORT.common_blind_spots(
                rows, expected, ["arm-metis", "datadog-saist"]
            ),
        )
```

- [ ] **Step 2: Run the tests and observe RED**

Run:

```powershell
uv run --with pyyaml python -m unittest -v tests.test_stage9_report
```

Expected: import fails because `scripts/stage9_report.py` does not exist.

- [ ] **Step 3: Implement pure metric and blind-spot functions**

Create `scripts/stage9_report.py` with:

```python
#!/usr/bin/env python3
"""Generate and validate the deterministic Stage 9 benchmark report."""

import re


_LESSON = re.compile(r"(?:^|/)lessons/([^/]+)/")


def approximate_f1(precision, hits, denominator):
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
```

- [ ] **Step 4: Add failing recommendation tests**

Append to `Stage9PureMetricTests`:

```python
    def test_recommendations_split_coverage_and_ci(self):
        quality = {
            "arm-metis": {
                "recall_lessons": 13,
                "tp": 148,
                "approximate_f1": 0.67,
                "precision": 0.77,
            },
            "datadog-saist": {
                "recall_lessons": 5,
                "tp": 41,
                "approximate_f1": 0.36,
                "precision": 0.87,
            },
        }
        resources = {
            "arm-metis": {"wall_clock_s": 58, "cost_usd": 0.445},
            "datadog-saist": {"wall_clock_s": 11, "cost_usd": 0.176},
        }
        result = REPORT.choose_recommendations(
            quality,
            resources,
            {"arm-metis": True, "datadog-saist": True},
        )
        self.assertEqual("arm-metis", result["deep_review"]["tool"])
        self.assertEqual("datadog-saist", result["ci_gate"]["tool"])
        self.assertEqual(
            {"per_pr": "datadog-saist", "nightly_or_release": "arm-metis"},
            result["combined"]["schedule"],
        )

    def test_recommendations_reject_no_pareto_candidate(self):
        with self.assertRaisesRegex(ValueError, "no Pareto-passing tool"):
            REPORT.choose_recommendations({}, {}, {})
```

- [ ] **Step 5: Run the recommendation test and observe RED**

Run:

```powershell
uv run --with pyyaml python -m unittest -v tests.test_stage9_report
```

Expected: FAIL because `choose_recommendations` is undefined.

- [ ] **Step 6: Implement deterministic recommendation selection**

Append to `scripts/stage9_report.py`:

```python
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
```

- [ ] **Step 7: Run Task 1 tests and diff checkpoint**

Run:

```powershell
uv run --with pyyaml python -m unittest -v tests.test_stage9_report
git diff --check -- scripts/stage9_report.py tests/test_stage9_report.py
```

Expected: all Task 1 tests pass and diff check exits 0. Do not stage or commit.

---

### Task 2: Add resource, final-quality, and provenance aggregation

**Files:**
- Modify: `scripts/stage9_report.py`
- Modify: `tests/test_stage9_report.py`

**Interfaces:**
- Consumes: Stage 4 and Stage 8 run rows, final judged rows, expected lessons.
- Produces:
  - `resource_summary(baseline_rows: list[dict], optimized_rows: list[dict], tools: list[str]) -> dict`
  - `quality_summary(rows: list[dict], expected: dict[str, str], tools: list[str], stable_min: int) -> dict`
  - `sha256_file(path: pathlib.Path) -> str`
  - `canonical_json(value: dict) -> str`

- [ ] **Step 1: Add failing resource and quality aggregation tests**

Add imports to `tests/test_stage9_report.py`:

```python
import json
import tempfile
```

Append a new class:

```python
class Stage9AggregationTests(unittest.TestCase):
    def test_resource_summary_recomputes_medians_and_deltas(self):
        baseline = [
            {
                "tool": "arm-metis",
                "wall_clock_s": 100,
                "input_tokens": 900,
                "output_tokens": 100,
                "cost_usd": 1.0,
            },
            {
                "tool": "arm-metis",
                "wall_clock_s": 120,
                "input_tokens": 1000,
                "output_tokens": 100,
                "cost_usd": 1.2,
            },
            {
                "tool": "arm-metis",
                "wall_clock_s": 110,
                "input_tokens": 950,
                "output_tokens": 100,
                "cost_usd": 1.1,
            },
        ]
        optimized = [
            {
                "tool": "arm-metis",
                "wall_clock_s": 50,
                "total_tokens": 500,
                "cost_usd": 0.5,
            },
            {
                "tool": "arm-metis",
                "wall_clock_s": 55,
                "total_tokens": 550,
                "cost_usd": 0.55,
            },
            {
                "tool": "arm-metis",
                "wall_clock_s": 60,
                "total_tokens": 600,
                "cost_usd": 0.6,
            },
        ]
        result = REPORT.resource_summary(
            baseline, optimized, ["arm-metis"]
        )["arm-metis"]
        self.assertEqual(
            {"wall_clock_s": 110, "total_tokens": 1050, "cost_usd": 1.1},
            result["baseline_median"],
        )
        self.assertEqual(
            {"wall_clock_s": 55, "total_tokens": 550, "cost_usd": 0.55},
            result["optimized_median"],
        )
        self.assertAlmostEqual(-0.5, result["relative_delta"]["wall_clock_s"])

    def test_quality_summary_recomputes_strict_recall_and_stability(self):
        expected = {"sqlinjection": "CWE-89", "ssrf": "CWE-918"}
        rows = [
            finding("arm-metis", "sqlinjection", "CWE-89", run_count=3),
            finding("arm-metis", "ssrf", "CWE-918", run_count=1),
            finding("arm-metis", "ssrf", "CWE-918", verdict="FP"),
        ]
        result = REPORT.quality_summary(
            rows, expected, ["arm-metis"], stable_min=3
        )["arm-metis"]
        self.assertEqual(3, result["judged"])
        self.assertEqual(2, result["tp"])
        self.assertEqual(1, result["fp"])
        self.assertEqual(2, result["recall_lessons"])
        self.assertEqual(1, result["stable_tp"])
        self.assertAlmostEqual(2 / 3, result["precision"])
```

- [ ] **Step 2: Run aggregation tests and observe RED**

Run:

```powershell
uv run --with pyyaml python -m unittest -v tests.test_stage9_report
```

Expected: FAIL because `resource_summary` and `quality_summary` are undefined.

- [ ] **Step 3: Implement resource and final-quality aggregation**

Add imports to `scripts/stage9_report.py`:

```python
import hashlib
import json
import pathlib
import statistics
```

Append:

```python
_RESOURCE_KEYS = ("wall_clock_s", "total_tokens", "cost_usd")


def _median_rows(rows, baseline):
    if not rows:
        raise ValueError("cannot calculate median for empty run rows")
    normalized = []
    for row in rows:
        value = dict(row)
        if baseline:
            value["total_tokens"] = (
                value["input_tokens"] + value["output_tokens"]
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
            key: (optimized[key] - base[key]) / base[key]
            for key in _RESOURCE_KEYS
        }
        result[tool] = {
            "baseline_median": base,
            "optimized_median": optimized,
            "relative_delta": delta,
        }
    return result


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
```

- [ ] **Step 4: Add failing canonical JSON and digest tests**

Append to `Stage9AggregationTests`:

```python
    def test_canonical_json_and_digest_are_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"
            path.write_bytes(b"abc\n")
            self.assertEqual(
                "edeaaff3f1774ad2888673770c6d64097e391bc362d7d6fb34982ddf0efd18cb",
                REPORT.sha256_file(path),
            )
        rendered = REPORT.canonical_json({"z": 1, "a": "Việt"})
        self.assertEqual(
            '{\n  "a": "Việt",\n  "z": 1\n}\n',
            rendered,
        )
```

- [ ] **Step 5: Run digest test and observe RED**

Run:

```powershell
uv run --with pyyaml python -m unittest -v tests.test_stage9_report
```

Expected: FAIL because `sha256_file` and `canonical_json` are undefined.

- [ ] **Step 6: Implement deterministic provenance primitives**

Append to `scripts/stage9_report.py`:

```python
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
    ) + "\n"
```

- [ ] **Step 7: Run Task 2 tests and diff checkpoint**

Run:

```powershell
uv run --with pyyaml python -m unittest -v tests.test_stage9_report
git diff --check -- scripts/stage9_report.py tests/test_stage9_report.py
```

Expected: all tests pass and diff check exits 0. Do not stage or commit.

---

### Task 3: Load, cross-validate, and build the real summary

**Files:**
- Modify: `scripts/stage9_report.py`
- Modify: `tests/test_stage9_report.py`

**Interfaces:**
- Consumes: repository root and every source listed in the design spec.
- Produces:
  - `source_paths(root: pathlib.Path, profile: str) -> dict[str, pathlib.Path]`
  - `load_sources(root: pathlib.Path) -> dict`
  - `build_summary_from_sources(sources: dict) -> dict`
  - `build_summary(root: pathlib.Path) -> dict`

- [ ] **Step 1: Add failing real-artifact integration test**

Append:

```python
class Stage9IntegrationTests(unittest.TestCase):
    def test_real_artifacts_build_expected_decision(self):
        summary = REPORT.build_summary(ROOT)
        self.assertEqual(1, summary["schema_version"])
        self.assertEqual(
            "c3ed45a733377bc7313b93f57ff518254d81380f",
            summary["benchmark"]["target_sha"],
        )
        self.assertEqual(
            "arm-metis",
            summary["recommendations"]["deep_review"]["tool"],
        )
        self.assertEqual(
            "datadog-saist",
            summary["recommendations"]["ci_gate"]["tool"],
        )
        self.assertEqual(6, summary["resources"]["run_count"])
        self.assertLessEqual(
            summary["budget"]["actual_spend_usd"],
            summary["budget"]["cap_usd"],
        )
        self.assertEqual(
            {
                "arm-metis",
                "datadog-saist",
            },
            set(summary["quality"]),
        )
        self.assertGreaterEqual(len(summary["provenance"]), 15)
        self.assertTrue(
            all(
                len(item["sha256"]) == 64
                and not Path(item["path"]).is_absolute()
                for item in summary["provenance"]
            )
        )
```

- [ ] **Step 2: Run integration test and observe RED**

Run:

```powershell
uv run --with pyyaml python -m unittest -v tests.test_stage9_report.Stage9IntegrationTests
```

Expected: FAIL because `build_summary` is undefined.

- [ ] **Step 3: Implement repository-confined loaders**

Add imports and script path setup:

```python
import os
import sys

import yaml


_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))

from stage7c_judge_agreement import agreement_metrics
```

Append:

```python
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
```

- [ ] **Step 4: Implement exact validation helpers**

Append:

```python
def _assert_equal(label, actual, expected):
    if actual != expected:
        raise ValueError(f"{label}: expected {expected!r}, got {actual!r}")


def _assert_close(label, actual, expected, tolerance=1e-9):
    if abs(float(actual) - float(expected)) > tolerance:
        raise ValueError(f"{label}: expected {expected!r}, got {actual!r}")


def _record_key(row):
    return (
        row["tool"],
        row["file"],
        row.get("line_min"),
        row["title"],
    )


def _validate_agreement(sources):
    weak = {_record_key(row): row for row in sources["judged_same"]}
    strong = {
        _record_key(row): row for row in sources["judged_independent"]
    }
    _assert_equal("same-model verdict count", len(weak), 238)
    _assert_equal("independent verdict count", len(strong), 238)
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
    return {
        "n": metrics["n"],
        "agreement": metrics["agreement"],
        "cohens_kappa": metrics["cohens_kappa"],
        "baseline_precision_by_tool": stored["precision_by_tool"],
        "scope": "Stage 7 baseline aligned verdict set",
    }
```

- [ ] **Step 5: Implement summary construction and all cross-checks**

Append:

```python
def build_summary_from_sources(sources):
    cfg = sources["config"]
    profile_name = cfg["stage8"]["active_profile"]
    profile = sources["profile"]
    final = sources["final"]
    tools = sorted(cfg["stage8"]["profiles"][profile_name])
    repeats = cfg["run"]["repeats"]
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

    for label, rows in (
        ("baseline", sources["baseline_cost"]),
        ("optimized", sources["optimized_cost"]),
    ):
        for tool in tools:
            tool_rows = [row for row in rows if row["tool"] == tool]
            _assert_equal(f"{label} run count for {tool}", len(tool_rows), repeats)
            for row in tool_rows:
                if "target_sha" in row:
                    _assert_equal(
                        f"{label} target SHA for {tool}",
                        row["target_sha"],
                        cfg["target"]["sha"],
                    )
                if row.get("via_fallback_calls", 0) != 0:
                    raise ValueError(f"{label} fallback calls for {tool}")
                if row.get("unknown_tool_calls", 0) != 0:
                    raise ValueError(f"{label} unknown calls for {tool}")

    counts = sources["counts"]
    for tool in tools:
        row = counts["per_tool"].get(tool)
        if not row:
            raise ValueError(f"Stage 6 count row missing for {tool}")
        if row["stable"] > row["unique"]:
            raise ValueError(f"Stage 6 stable count exceeds unique for {tool}")

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
        if not (
            stored["quality_gate"]["passed"]
            and stored["resource_gate"]["passed"]
            and stored["pareto_passed"]
        ):
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
    _assert_equal(
        "novel usage complete",
        state["usage"]["usage_complete"],
        True,
    )
    if state.get("item_errors"):
        raise ValueError("novel judge contains item errors")

    run_spend = sum(row["cost_usd"] for row in sources["optimized_cost"])
    judge_spend = float(state["estimated_cost_usd"])
    actual_spend = run_spend + judge_spend
    _assert_close("final run spend", final["run_spend_usd"], run_spend)
    _assert_close("final judge spend", final["judge_spend_usd"], judge_spend)
    _assert_close("final actual spend", final["actual_spend_usd"], actual_spend)
    _assert_equal("final budget passed", final["budget_passed"], True)
    if actual_spend > cfg["stage8"]["budget_usd"]:
        raise ValueError(
            f"Stage 8 spend {actual_spend} exceeds cap "
            f"{cfg['stage8']['budget_usd']}"
        )

    sensitivity = _validate_agreement(sources)
    same_precision = sources["precision_same"]
    independent_precision = sources["precision_independent"]
    _assert_equal("same judge scope", same_precision["scope"], cfg["judge"]["scope"])
    _assert_equal(
        "independent judge scope",
        independent_precision["scope"],
        cfg["judge"]["scope"],
    )
    for tool in tools:
        for label, rows, stored in (
            (
                "same-model",
                sources["judged_same"],
                same_precision["per_tool"][tool]["precision"],
            ),
            (
                "independent",
                sources["judged_independent"],
                independent_precision["per_tool"][tool]["precision"],
            ),
        ):
            tool_rows = [row for row in rows if row["tool"] == tool]
            exact = (
                sum(row["verdict"] == "TP" for row in tool_rows)
                / len(tool_rows)
            )
            _assert_equal(
                f"legacy {label} precision {tool}",
                stored,
                round(exact, 4),
            )

    recommendations = choose_recommendations(quality, {
        tool: resources[tool]["optimized_median"] for tool in tools
    }, pareto)
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
```

- [ ] **Step 6: Add failing corruption tests**

Add imports:

```python
import copy
```

Append to `Stage9IntegrationTests`:

```python
    def test_builder_rejects_failed_pareto_gate(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["final"]["per_tool"]["arm-metis"]["pareto_passed"] = False
        with self.assertRaisesRegex(
            ValueError, "finalist did not pass every Pareto gate"
        ):
            REPORT.build_summary_from_sources(broken)

    def test_builder_rejects_incomplete_judge_usage(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["judge_state"]["usage"]["usage_complete"] = False
        with self.assertRaisesRegex(ValueError, "novel usage complete"):
            REPORT.build_summary_from_sources(broken)
```

- [ ] **Step 7: Run Task 3 tests and diff checkpoint**

Run:

```powershell
uv run --with pyyaml python -m unittest -v tests.test_stage9_report
git diff --check -- scripts/stage9_report.py tests/test_stage9_report.py
```

Expected: tests pass, real summary selects Metis for deep review and SAIST for
CI, and diff check exits 0. Do not stage or commit.

---

### Task 4: Render deterministic Markdown and implement `--write` / `--check`

**Files:**
- Modify: `scripts/stage9_report.py`
- Modify: `tests/test_stage9_report.py`
- Generate: `results/report/final-summary.json`
- Generate: `docs/stage9-bao-cao-cuoi.md`

**Interfaces:**
- Consumes: the schema-version-1 summary dictionary.
- Produces:
  - `render_markdown(summary: dict) -> str`
  - `write_outputs(root: pathlib.Path, summary: dict) -> None`
  - `check_outputs(root: pathlib.Path, summary: dict) -> None`
  - CLI `--write` and `--check`

- [ ] **Step 1: Add failing deterministic renderer tests**

Append:

```python
class Stage9RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = REPORT.build_summary(ROOT)

    def test_markdown_contains_two_scenario_decisions_and_caveats(self):
        text = REPORT.render_markdown(self.summary)
        self.assertTrue(text.startswith("# Giai đoạn 9 — Báo cáo cuối"))
        self.assertIn("arm-metis", text)
        self.assertIn("datadog-saist", text)
        self.assertIn("Cohen", text)
        self.assertIn("mức lesson", text)
        self.assertIn("Không có người thắng tuyệt đối", text)
        self.assertTrue(text.endswith("\n"))

    def test_renderer_is_deterministic(self):
        first = REPORT.render_markdown(self.summary)
        second = REPORT.render_markdown(copy.deepcopy(self.summary))
        self.assertEqual(first, second)
```

- [ ] **Step 2: Run renderer tests and observe RED**

Run:

```powershell
uv run --with pyyaml python -m unittest -v tests.test_stage9_report.Stage9RenderTests
```

Expected: FAIL because `render_markdown` is undefined.

- [ ] **Step 3: Implement the complete Markdown renderer**

Append:

```python
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
    lines = [
        "# Giai đoạn 9 — Báo cáo cuối",
        "",
        "> ✅ **KẾT LUẬN:** Không có người thắng tuyệt đối. "
        f"`{deep}` phù hợp review chuyên sâu; `{ci}` phù hợp CI trên mỗi PR.",
        "",
        f"> **Review chuyên sâu:** dùng `{deep}` để ưu tiên độ bao phủ, "
        "sau đó review thủ công các finding.",
        "",
        f"> **CI gate:** dùng `{ci}` trên mỗi PR; chạy `{deep}` nightly "
        "hoặc trước release.",
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
    lines.extend([
        "",
        "## 6. Khuyến nghị triển khai",
        "",
        f"- Mỗi PR: `{ci}`.",
        f"- Nightly hoặc trước release: `{deep}`.",
        "- Finding từ Metis cần review thủ công; không dùng count thô để block merge.",
        "- Hai tool bổ sung nhau; báo cáo không tạo weighted score.",
        "",
        "## 7. Giới hạn phương pháp",
        "",
    ])
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
```

- [ ] **Step 4: Add failing write/check tests**

Append to `Stage9RenderTests`:

```python
    def test_check_outputs_detects_stale_file_without_writing(self):
        expected_json = REPORT.canonical_json(self.summary)
        expected_md = REPORT.render_markdown(self.summary)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "results" / "report").mkdir(parents=True)
            (root / "docs").mkdir()
            (root / "results" / "report" / "final-summary.json").write_text(
                expected_json, encoding="utf-8", newline="\n"
            )
            report = root / "docs" / "stage9-bao-cao-cuoi.md"
            report.write_text("stale\n", encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(ValueError, "stale generated output"):
                REPORT.check_outputs(root, self.summary)
            self.assertEqual("stale\n", report.read_text(encoding="utf-8"))

    def test_write_then_check_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            REPORT.write_outputs(root, self.summary)
            REPORT.check_outputs(root, self.summary)
            self.assertEqual(
                REPORT.canonical_json(self.summary),
                (root / "results" / "report" / "final-summary.json").read_text(
                    encoding="utf-8"
                ),
            )
```

- [ ] **Step 5: Run output tests and observe RED**

Run:

```powershell
uv run --with pyyaml python -m unittest -v tests.test_stage9_report.Stage9RenderTests
```

Expected: FAIL because `write_outputs` and `check_outputs` are undefined.

- [ ] **Step 6: Implement atomic output and stale checks**

Append:

```python
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
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            raise ValueError(f"stale generated output: {relative.as_posix()}")
```

- [ ] **Step 7: Implement mutually exclusive CLI**

Add import:

```python
import argparse
```

Append:

```python
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
```

- [ ] **Step 8: Run tests, generate outputs, and verify check mode**

Run:

```powershell
uv run --with pyyaml python -m unittest -v tests.test_stage9_report
uv run --with pyyaml python scripts/stage9_report.py --write
uv run --with pyyaml python scripts/stage9_report.py --check
```

Expected: tests pass, both output paths print under `WROTE`, then
`STAGE9 REPORT OK`.

- [ ] **Step 9: Record a diff checkpoint**

Run:

```powershell
git diff --check -- scripts/stage9_report.py tests/test_stage9_report.py docs/stage9-bao-cao-cuoi.md
```

Expected: exit 0. Do not stage or commit.

---

### Task 5: Complete roadmap documentation and final verification

**Files:**
- Modify: `README.md`
- Modify: `docs/00-tong-quan.md`
- Verify: `docs/stage9-bao-cao-cuoi.md`
- Verify: `results/report/final-summary.json`

**Interfaces:**
- Consumes: generated Stage 9 report and summary.
- Produces: completed roadmap links and a fully verified Stage 9 handoff.

- [ ] **Step 1: Add a failing documentation contract test**

Append to `tests/test_stage9_report.py`:

```python
class Stage9DocumentationTests(unittest.TestCase):
    def test_roadmap_links_completed_stage9_report(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        overview = (ROOT / "docs" / "00-tong-quan.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "| 9 | Báo cáo | ✅ Hoàn tất | "
            "[docs/stage9-bao-cao-cuoi.md](docs/stage9-bao-cao-cuoi.md) |",
            readme,
        )
        self.assertIn(
            "[stage9](stage9-bao-cao-cuoi.md)",
            overview,
        )
        self.assertNotIn("8→9. Tối ưu, báo cáo (dựng sau).", overview)
```

- [ ] **Step 2: Run the documentation test and observe RED**

Run:

```powershell
uv run --with pyyaml python -m unittest -v tests.test_stage9_report.Stage9DocumentationTests
```

Expected: FAIL because README still says Stage 9 is pending and Stage 0 says
Stage 8–9 are future work.

- [ ] **Step 3: Update README Stage 9 row**

Apply this exact replacement:

```diff
-| 9 | Báo cáo | ⏳ Chưa | — |
+| 9 | Báo cáo | ✅ Hoàn tất | [docs/stage9-bao-cao-cuoi.md](docs/stage9-bao-cao-cuoi.md) |
```

- [ ] **Step 4: Update the Stage 0 roadmap ending**

Replace:

```markdown
8→9. Tối ưu, báo cáo (dựng sau).
```

with:

```markdown
8. **Tối ưu Pareto** ([stage8](stage8-toi-uu-pareto.md)) — giảm resource trong
   các quality floor đã chốt, giữ baseline nguyên vẹn.
9. **Báo cáo cuối** ([stage9](stage9-bao-cao-cuoi.md)) — tổng hợp quality,
   resource, độ nhạy judge và khuyến nghị theo tình huống.
```

- [ ] **Step 5: Run documentation and full unit tests**

Run:

```powershell
uv run --with pyyaml python -m unittest discover -s tests -v
```

Expected: all tests pass with 0 failures.

- [ ] **Step 6: Run fresh compile, YAML, generated-output, and shell checks**

Run:

```powershell
uv run --with pyyaml python -m compileall -q scripts proxy/token_logger.py
uv run --with pyyaml python -c "import yaml,pathlib; [yaml.safe_load(p.read_text(encoding='utf-8')) for p in [pathlib.Path('config/benchmark.yaml'),pathlib.Path('proxy/litellm_config.yaml')]]"
uv run --with pyyaml python scripts/stage9_report.py --check
& 'C:\Program Files\Git\bin\bash.exe' -n scripts/*.sh adapters/*.sh
git diff --check
```

Expected: every command exits 0 and the report checker prints
`STAGE9 REPORT OK`.

- [ ] **Step 7: Validate the final decision package directly**

Run:

```powershell
uv run python -c "import json,pathlib; p=json.loads(pathlib.Path('results/report/final-summary.json').read_text(encoding='utf-8')); assert p['schema_version']==1; assert p['recommendations']['deep_review']['tool']=='arm-metis'; assert p['recommendations']['ci_gate']['tool']=='datadog-saist'; assert p['recommendations']['universal_winner'] is None; assert p['budget']['passed']; assert p['resources']['run_count']==6; assert len(p['provenance'])>=15; print('STAGE9 RESULTS OK')"
```

Expected: `STAGE9 RESULTS OK`.

- [ ] **Step 8: Preserve and report the dirty worktree**

Run:

```powershell
git status --short
```

Report Stage 9 files separately:

- `scripts/stage9_report.py`
- `tests/test_stage9_report.py`
- `docs/stage9-bao-cao-cuoi.md`
- `results/report/final-summary.json`
- `README.md`
- `docs/00-tong-quan.md`
- Stage 9 spec and plan under `docs/superpowers/`

Do not commit, stage, push, reset, or clean.
