# Stage 8 Pareto Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a budget-capped `balanced-v1` optimization profile for Metis and SAIST, then accept it only when it improves the resource Pareto frontier within the approved quality tolerances.

**Architecture:** Stage 8 adds optional adapter knobs and reuses the Stage 4 runner with a protected output root. A Python evaluator reuses Stage 5/6 normalization, baseline independent verdicts, and Stage 7c batch judging for novel findings; all Stage 8 artifacts live below `results/optimization/`.

**Tech Stack:** Bash, Python 3, PyYAML through `uv`, `unittest`, LiteLLM proxy logs, SARIF/JSONL, Gemini Batch API.

## Global Constraints

- Target SHA stays `c3ed45a733377bc7313b93f57ff518254d81380f`.
- Model stays `gemini-3.1-flash-lite`, temperature 0, top_p 1.
- Main scope stays Java, excluding tests and vendor code.
- Finalist uses three runs: run 1 cold, runs 2–3 warm.
- Stage 8 API hard cap is `$5.00`, including a `$0.25` reserve until novel-finding judge cost is known.
- Quality floors: Metis precision 0.751, recall 12/22, stable TP 144; SAIST precision 0.871, recall 4/22, stable TP 28.
- Resource gate: at least one median improves by 10%; no other median regresses by more than 5%.
- Never overwrite `results/findings/`, `results/stats/precision*.json`, or Stage 4–7 artifacts.
- Work in the current checkout because the required Stage 4–7 pipeline is uncommitted. Do not commit or alter the existing staged index unless the user explicitly asks; use test and diff checkpoints instead.

---

### Task 1: Add the Stage 8 profile and typed config access

**Files:**
- Modify: `config/benchmark.yaml`
- Modify: `scripts/bench_config.py`
- Create: `tests/test_stage8_config.py`

**Interfaces:**
- Consumes: existing `bench_config.load()` and YAML single source of truth.
- Produces: `stage8_profile(cfg, name) -> dict` and CLI `stage8-profile NAME TOOL` emitting one JSON object.

- [ ] **Step 1: Write the failing config tests**

```python
import importlib.util
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bench_config", ROOT / "scripts/bench_config.py")
CFG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CFG)

class Stage8ConfigTests(unittest.TestCase):
    def test_balanced_profile_has_approved_knobs_and_gates(self):
        cfg = CFG.load()
        profile = CFG.stage8_profile(cfg, "balanced-v1")
        self.assertEqual(5.0, cfg["stage8"]["budget_usd"])
        self.assertEqual(12, profile["arm-metis"]["max_workers"])
        self.assertEqual("*.java", profile["arm-metis"]["review_include"])
        self.assertEqual(25, profile["datadog-saist"]["file_concurrency"])
        self.assertEqual(0.751, cfg["stage8"]["quality_floor"]["arm-metis"]["precision"])

    def test_unknown_profile_is_rejected(self):
        with self.assertRaisesRegex(KeyError, "unknown stage8 profile"):
            CFG.stage8_profile(CFG.load(), "missing")
```

- [ ] **Step 2: Run the tests and observe RED**

Run: `uv run --with pyyaml python -m unittest -v tests.test_stage8_config`

Expected: FAIL because `stage8_profile` and the `stage8` YAML block do not exist.

- [ ] **Step 3: Add the approved YAML block**

```yaml
stage8:
  active_profile: balanced-v1
  budget_usd: 5.0
  judge_reserve_usd: 0.25
  min_resource_improvement: 0.10
  max_resource_regression: 0.05
  quality_floor:
    arm-metis: {precision: 0.751, recall_lessons: 12, stable_tp: 144}
    datadog-saist: {precision: 0.871, recall_lessons: 4, stable_tp: 28}
  profiles:
    balanced-v1:
      arm-metis:
        max_workers: 12
        review_include: "*.java"
        review_exclude: ["**/src/test/", "**/src/it/", "**/test/", "**/tests/"]
      datadog-saist:
        file_concurrency: 25
```

- [ ] **Step 4: Implement typed lookup and CLI output**

```python
import json

def stage8_profile(cfg, name):
    profiles = (cfg.get("stage8") or {}).get("profiles") or {}
    if name not in profiles:
        raise KeyError(f"unknown stage8 profile: {name}")
    return profiles[name]

# In main():
elif cmd == "stage8-profile":
    profile = stage8_profile(cfg, sys.argv[2])
    tool = sys.argv[3]
    if tool not in profile:
        sys.exit(f"[bench_config] profile '{sys.argv[2]}' has no tool '{tool}'")
    print(json.dumps(profile[tool], separators=(",", ":")))
```

- [ ] **Step 5: Run the tests and config smoke checks**

Run:

```bash
uv run --with pyyaml python -m unittest -v tests.test_stage8_config
uv run --with pyyaml python scripts/bench_config.py stage8-profile balanced-v1 arm-metis
```

Expected: tests PASS; CLI prints JSON containing `max_workers:12`.

- [ ] **Step 6: Record a diff checkpoint**

Run: `git diff --check -- config/benchmark.yaml scripts/bench_config.py tests/test_stage8_config.py`

Expected: exit 0. Do not commit because the existing index contains unrelated staged work.

---

### Task 2: Make adapter tuning optional and isolate runner output

**Files:**
- Modify: `adapters/arm-metis.sh`
- Modify: `adapters/datadog-saist.sh`
- Modify: `scripts/stage4_run.sh`
- Create: `tests/test_stage8_shell_contracts.py`

**Interfaces:**
- Consumes: `METIS_MAX_WORKERS`, `METIS_REVIEW_INCLUDE`, newline-separated `METIS_REVIEW_EXCLUDES`, and `SAIST_FILE_CONCURRENCY`.
- Produces: unchanged Stage 4 behavior when variables are absent; `stage4_run.sh --output-root ABS_PATH --run-index N` for isolated single runs.

- [ ] **Step 1: Write failing shell-contract tests**

```python
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class Stage8ShellContractTests(unittest.TestCase):
    def test_adapters_expose_optional_stage8_knobs(self):
        metis = (ROOT / "adapters/arm-metis.sh").read_text(encoding="utf-8")
        saist = (ROOT / "adapters/datadog-saist.sh").read_text(encoding="utf-8")
        self.assertIn("METIS_MAX_WORKERS", metis)
        self.assertIn("METIS_REVIEW_INCLUDE", metis)
        self.assertIn("METIS_REVIEW_EXCLUDES", metis)
        self.assertIn("SAIST_FILE_CONCURRENCY", saist)

    def test_stage4_runner_supports_protected_output_and_one_run(self):
        runner = (ROOT / "scripts/stage4_run.sh").read_text(encoding="utf-8")
        self.assertIn("--output-root", runner)
        self.assertIn("--run-index", runner)
        self.assertIn("BENCH_RESULTS_ROOT", runner)
```

- [ ] **Step 2: Run the tests and observe RED**

Run: `uv run python -m unittest -v tests.test_stage8_shell_contracts`

Expected: FAIL because the variables/options are absent.

- [ ] **Step 3: Add optional Metis engine YAML**

Before writing `metis.yaml`, build an empty-by-default block:

```bash
METIS_ENGINE_BLOCK=""
if [[ -n "${METIS_MAX_WORKERS:-}" || -n "${METIS_REVIEW_INCLUDE:-}" ]]; then
  METIS_ENGINE_BLOCK="metis_engine:"
  [[ -n "${METIS_MAX_WORKERS:-}" ]] \
    && METIS_ENGINE_BLOCK+=$'\n  max_workers: '"$METIS_MAX_WORKERS"
  if [[ -n "${METIS_REVIEW_INCLUDE:-}" ]]; then
    METIS_ENGINE_BLOCK+=$'\n  review_code_include_paths:\n    - '"\"$METIS_REVIEW_INCLUDE\""
  fi
  if [[ -n "${METIS_REVIEW_EXCLUDES:-}" ]]; then
    METIS_ENGINE_BLOCK+=$'\n  review_code_exclude_paths:'
    while IFS= read -r pattern; do
      [[ -n "$pattern" ]] && METIS_ENGINE_BLOCK+=$'\n    - '"\"$pattern\""
    done <<<"$METIS_REVIEW_EXCLUDES"
  fi
fi
```

Insert `$METIS_ENGINE_BLOCK` before `llm_provider:`. With no variables, the generated YAML remains behaviorally identical to Stage 4.

- [ ] **Step 4: Add the optional SAIST flag**

```bash
SAIST_TUNING_ARGS=()
if [[ -n "${SAIST_FILE_CONCURRENCY:-}" ]]; then
  SAIST_TUNING_ARGS+=(--file-concurrency "$SAIST_FILE_CONCURRENCY")
fi

# Append before --local-prompts:
"${SAIST_TUNING_ARGS[@]}" \
--local-prompts
```

- [ ] **Step 5: Add isolated run options to Stage 4**

```bash
OUTPUT_ROOT="${BENCH_RESULTS_ROOT:-$ROOT_DIR/results/findings}"
RUN_INDEX=""

# argument cases:
--output-root) OUTPUT_ROOT="$2"; shift 2 ;;
--run-index) RUN_INDEX="$2"; shift 2 ;;

# in run_once:
local run_dir="$OUTPUT_ROOT/$tool/run-$(printf '%02d' "$idx")-$phase"

# in main, replace seq selection:
local run_indices
run_indices="$( [[ -n "$RUN_INDEX" ]] && echo "$RUN_INDEX" || seq 1 "$REPEATS" )"
for i in $run_indices; do
```

Reject an output root that resolves to `results/findings` when `BENCH_RESULTS_ROOT` was explicitly supplied, and create the selected root before running.

- [ ] **Step 6: Run tests and shell syntax checks**

Run:

```bash
uv run python -m unittest -v tests.test_stage8_shell_contracts
bash -n scripts/stage4_run.sh adapters/*.sh
```

Expected: PASS and exit 0.

- [ ] **Step 7: Record a diff checkpoint**

Run: `git diff --check -- adapters scripts/stage4_run.sh tests/test_stage8_shell_contracts.py`

Expected: exit 0; do not stage or commit.

---

### Task 3: Implement budget, verdict matching, quality gates, and Pareto gates

**Files:**
- Create: `scripts/stage8_evaluate.py`
- Create: `tests/test_stage8_evaluate.py`

**Interfaces:**
- Consumes: Stage 8 run directories, proxy call log, baseline `judged-independent.jsonl`, ground-truth lessons, and Stage 8 config.
- Produces: `match_baseline`, `budget_allows`, `quality_metrics`, `resource_gate`, and CLI output under `results/optimization/{profile}/stats/`.

- [ ] **Step 1: Write failing evaluator tests**

```python
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("stage8_evaluate", ROOT / "scripts/stage8_evaluate.py")
EVAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVAL)

def rec(line=10, verdict="TP", exact=True):
    return {"tool":"arm-metis","file":"src/A.java","title":"SQL Injection",
            "line_min":line,"line_confidence":"exact" if exact else "unreliable",
            "verdict":verdict,"run_count":3,"judge_cwe":"CWE-89","cwe":"CWE-89"}

class Stage8EvaluateTests(unittest.TestCase):
    def test_match_baseline_uses_title_and_line_tolerance(self):
        self.assertEqual("TP", EVAL.match_baseline(rec(17), [rec(10)], 10)["verdict"])
        self.assertIsNone(EVAL.match_baseline(rec(21), [rec(10)], 10))

    def test_unreliable_line_does_not_block_same_finding(self):
        self.assertEqual("TP", EVAL.match_baseline(rec(200, exact=False), [rec(10)], 10)["verdict"])

    def test_budget_keeps_judge_reserve(self):
        self.assertTrue(EVAL.budget_allows(3.5, 1.2, 0.25, 5.0))
        self.assertFalse(EVAL.budget_allows(3.7, 1.2, 0.25, 5.0))

    def test_resource_gate_needs_ten_percent_gain_without_five_percent_regression(self):
        baseline={"wall_clock_s":100,"total_tokens":1000,"cost_usd":1.0}
        self.assertTrue(EVAL.resource_gate(baseline,{"wall_clock_s":88,"total_tokens":1010,"cost_usd":1.01},.10,.05)["passed"])
        self.assertFalse(EVAL.resource_gate(baseline,{"wall_clock_s":88,"total_tokens":1060,"cost_usd":1.0},.10,.05)["passed"])

    def test_quality_gate_applies_tool_specific_floors(self):
        floors={"precision":.751,"recall_lessons":12,"stable_tp":144}
        self.assertTrue(EVAL.quality_gate({"precision":.76,"recall_lessons":12,"stable_tp":144},floors)["passed"])
        self.assertFalse(EVAL.quality_gate({"precision":.75,"recall_lessons":12,"stable_tp":144},floors)["passed"])
```

- [ ] **Step 2: Run tests and observe RED**

Run: `uv run --with pyyaml python -m unittest -v tests.test_stage8_evaluate`

Expected: import fails because `stage8_evaluate.py` does not exist.

- [ ] **Step 3: Implement pure gate functions**

```python
import statistics
from stage6_dedup import norm_title

def match_baseline(candidate, baseline, tolerance):
    matches=[]
    for old in baseline:
        if old["tool"] != candidate["tool"] or old["file"] != candidate["file"]:
            continue
        if norm_title(old["title"]) != norm_title(candidate["title"]):
            continue
        both_exact = old.get("line_confidence") == candidate.get("line_confidence") == "exact"
        if both_exact and abs((old.get("line_min") or 0)-(candidate.get("line_min") or 0)) > tolerance:
            continue
        matches.append(old)
    if not matches:
        return None
    return min(matches, key=lambda r: abs((r.get("line_min") or 0)-(candidate.get("line_min") or 0)))

def budget_allows(actual, projected, reserve, cap):
    return actual + projected + reserve <= cap

def quality_gate(metrics, floors):
    checks={k: metrics[k] >= floors[k] for k in ("precision","recall_lessons","stable_tp")}
    return {"passed": all(checks.values()), "checks": checks}

def resource_gate(baseline, candidate, min_gain, max_regression):
    delta={k:(candidate[k]-baseline[k])/baseline[k] for k in baseline}
    improved=any(v <= -min_gain for v in delta.values())
    bounded=all(v <= max_regression for v in delta.values())
    return {"passed": improved and bounded, "relative_delta": delta}
```

- [ ] **Step 4: Implement run loading and conservative screening**

Use `stage5_normalize.normalize_result`, `stage5_normalize.cost_for_run`, and `stage6_dedup.dedup_tool` directly. Write:

```python
def load_profile_runs(profile_dir, calls):
    rows, resources = [], []
    for tool_dir in sorted(p for p in profile_dir.iterdir() if p.is_dir()):
        for run_dir in sorted(p for p in tool_dir.iterdir() if p.is_dir()):
            meta_path, sarif_path = run_dir / "run_meta.json", run_dir / "raw_output.sarif"
            if not meta_path.exists() or not sarif_path.exists():
                raise ValueError(f"missing run artifact: {run_dir}")
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("valid") is not True:
                raise ValueError(f"invalid run manifest: {run_dir}")
            sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
            cost = cost_for_run(calls, meta)
            if not cost or cost["via_fallback_calls"] or cost["unknown_tool_calls"]:
                raise ValueError(f"invalid proxy accounting: {run_dir}")
            resources.append({"tool": tool_dir.name, "run": run_dir.name,
                              "wall_clock_s": meta["wall_clock_s"], **cost})
            for sarif_run in sarif.get("runs", []):
                for result in sarif_run.get("results", []):
                    rows.append(normalize_result(result, tool_dir.name, run_dir.name,
                                                 meta.get("phase")))
    return rows, resources

def inherit_or_conservative_fp(candidate, baseline, tolerance):
    old = match_baseline(candidate, baseline, tolerance)
    if old:
        return {**candidate, **{k: old.get(k) for k in
                ("verdict","judge_cwe","judge_reason","judge_alias")},
                "verdict_source":"baseline-independent"}
    return {**candidate, "verdict":"FP", "verdict_source":"screening-conservative"}
```

Import `json`, `normalize_result`, and `cost_for_run`; JSON decode failures retain
the offending path by wrapping them as `ValueError(f"invalid SARIF: {sarif_path}")`.

- [ ] **Step 5: Implement lesson recall, stable TP, medians, and JSON outputs**

```python
def median_resources(run_resources):
    keys=("wall_clock_s","total_tokens","cost_usd")
    return {k: statistics.median(r[k] for r in run_resources) for k in keys}

def quality_metrics(judged, expected_lessons, stable_min):
    scoped=[r for r in judged if r.get("in_scope", True)]
    tp=[r for r in scoped if r["verdict"] == "TP"]
    return {
        "precision": len(tp)/len(scoped) if scoped else 0.0,
        "recall_lessons": count_expected_lesson_hits(tp, expected_lessons),
        "stable_tp": sum(r.get("run_count",0) >= stable_min for r in tp),
    }
```

CLI form:

```bash
uv run --with pyyaml python scripts/stage8_evaluate.py \
  --profile balanced-v1 --mode screening
```

Write `screening.json`, `cost_by_run.json`, `findings.jsonl`, and `deduped.jsonl` below `results/optimization/balanced-v1/stats/`.

- [ ] **Step 6: Run evaluator tests and full existing tests**

Run:

```bash
uv run --with pyyaml python -m unittest -v tests.test_stage8_evaluate
uv run --with pyyaml python -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 7: Record a diff checkpoint**

Run: `git diff --check -- scripts/stage8_evaluate.py tests/test_stage8_evaluate.py`

Expected: exit 0; do not stage or commit.

---

### Task 4: Generalize Gemini Batch judging for novel finalist findings

**Files:**
- Modify: `scripts/stage7_batch_judge.py`
- Modify: `tests/test_stage7_batch_judge.py`
- Modify: `scripts/stage8_evaluate.py`
- Modify: `tests/test_stage8_evaluate.py`

**Interfaces:**
- Consumes: arbitrary source JSONL, output JSONL, and state JSON paths inside the repository.
- Produces: Stage 7 defaults unchanged plus CLI `--source`, `--out`, `--state`; evaluator command `--prepare-novel-judge`.

- [ ] **Step 1: Add failing path-override tests**

```python
def test_resolve_paths_keeps_stage7_defaults_and_accepts_stage8_overrides(self):
    defaults = BATCH.resolve_paths(None, None, None)
    self.assertTrue(defaults[0].endswith("deduped.jsonl"))
    custom = BATCH.resolve_paths("results/optimization/p/stats/novel.jsonl",
                                 "results/optimization/p/stats/judged-novel.jsonl",
                                 "results/optimization/p/stats/judge-state.json")
    self.assertTrue(custom[1].endswith("judged-novel.jsonl"))
    self.assertTrue(custom[2].endswith("judge-state.json"))
```

Add an evaluator test asserting that only unmatched findings are written to `novel.jsonl`.

- [ ] **Step 2: Run targeted tests and observe RED**

Run:

```bash
uv run --with pyyaml python -m unittest -v \
  tests.test_stage7_batch_judge tests.test_stage8_evaluate
```

Expected: FAIL because `resolve_paths` and novel preparation do not exist.

- [ ] **Step 3: Add repository-confined path resolution**

```python
def _repo_path(value, default):
    path = os.path.abspath(os.path.join(_ROOT, value)) if value else default
    if os.path.commonpath([_ROOT, path]) != _ROOT:
        raise ValueError(f"path escapes repository: {value}")
    return path

def resolve_paths(source=None, out=None, state=None):
    return (_repo_path(source, _DEFAULT_SOURCE),
            _repo_path(out, _DEFAULT_OUT),
            _repo_path(state, _DEFAULT_STATE))
```

Change the existing function definitions and their call sites with this exact diff:

```diff
-def _load_state():
+def _load_state(state_path):
-def _load_work():
+def _load_work(source_path, out_path):
-def submit(model, alias, effort):
+def submit(model, alias, effort, source_path, out_path, state_path):
-def status():
+def status(state_path):
-def collect():
+def collect(source_path, out_path, state_path):
```

Dispatch the resolved paths exactly once in `main`:

```python
if args.submit:
    submit(args.model, args.alias, args.reasoning_effort,
           source_path, out_path, state_path)
elif args.status:
    status(state_path)
else:
    collect(source_path, out_path, state_path)
```

`fetch_job(state)` remains path-independent. Preserve all existing default values
through `resolve_paths` so the Stage 7 commands need no new arguments.

- [ ] **Step 4: Add CLI arguments**

```python
ap.add_argument("--source")
ap.add_argument("--out")
ap.add_argument("--state")
source_path, out_path, state_path = resolve_paths(args.source, args.out, args.state)
```

Every action must use the resolved paths. A second submit must still be rejected while the selected state says `collected:false`.

- [ ] **Step 5: Prepare and merge novel verdicts**

`stage8_evaluate.py --prepare-novel-judge` writes only unmatched finalist records. After batch collect, `--mode final` merges `judged-novel.jsonl` with inherited baseline-independent verdicts, rejects duplicate/missing keys, and refuses to calculate a final gate unless every candidate has TP/FP.

- [ ] **Step 6: Run targeted and regression tests**

Run: `uv run --with pyyaml python -m unittest discover -s tests -v`

Expected: all tests PASS, including original Stage 7 default-path tests.

- [ ] **Step 7: Record a diff checkpoint**

Run: `git diff --check -- scripts/stage7_batch_judge.py scripts/stage8_evaluate.py tests`

Expected: exit 0; do not stage or commit.

---

### Task 5: Build the Stage 8 orchestrator and budget preflight

**Files:**
- Create: `scripts/stage8_run.sh`
- Create: `tests/test_stage8_runner.py`
- Modify: `README.md`
- Create: `docs/stage8-toi-uu-pareto.md`

**Interfaces:**
- Consumes: Stage 8 profile JSON, Stage 4 isolated single-run options, evaluator budget check.
- Produces: `--dry-run`, `--screen`, `--complete`, `--tool`, and a profile manifest at `results/optimization/{profile}/profile.json`.

- [ ] **Step 1: Write failing runner contract tests**

```python
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class Stage8RunnerTests(unittest.TestCase):
    def test_runner_has_budget_and_isolated_output_guards(self):
        text=(ROOT/"scripts/stage8_run.sh").read_text(encoding="utf-8")
        self.assertIn("results/optimization", text)
        self.assertIn("budget-check", text)
        self.assertIn("--run-index", text)
        self.assertIn("BENCH_RESULTS_ROOT", text)
        self.assertNotIn("rm -rf \"$ROOT_DIR/results/findings\"", text)
```

- [ ] **Step 2: Run test and observe RED**

Run: `uv run python -m unittest -v tests.test_stage8_runner`

Expected: ERROR because `stage8_run.sh` does not exist.

- [ ] **Step 3: Implement profile export and protected paths**

```bash
PROFILE="balanced-v1"
MODE=""
ONLY_TOOL=""
OUT_ROOT="$ROOT_DIR/results/optimization/$PROFILE/runs"
[[ "$OUT_ROOT" == "$ROOT_DIR/results/optimization/"* ]] \
  || { c_err "Stage 8 output escaped results/optimization"; exit 1; }
export BENCH_RESULTS_ROOT="$OUT_ROOT"
```

Parse the profile JSON with `uv run --with pyyaml python -c` or add scalar CLI fields; export:

```bash
export METIS_MAX_WORKERS=12
export METIS_REVIEW_INCLUDE='*.java'
export METIS_REVIEW_EXCLUDES=$'**/src/test/\n**/src/it/\n**/test/\n**/tests/'
export SAIST_FILE_CONCURRENCY=25
```

The actual values must come from `benchmark.yaml`; the literal block above is the expected exported result used by dry-run assertions.

- [ ] **Step 4: Implement screening and completion loops**

```bash
run_index() {
  local tool="$1" index="$2"
  uv run --with pyyaml python "$ROOT_DIR/scripts/stage8_evaluate.py" \
    --profile "$PROFILE" --budget-check --tool "$tool" --next-run "$index"
  bash "$ROOT_DIR/scripts/stage4_run.sh" --tool "$tool" --run-index "$index" \
    --output-root "$OUT_ROOT"
}

case "$MODE" in
  screen)
    for tool in $(selected_tools); do run_index "$tool" 1; done
    uv run --with pyyaml python "$ROOT_DIR/scripts/stage8_evaluate.py" \
      --profile "$PROFILE" --mode screening
    ;;
  complete)
    uv run --with pyyaml python "$ROOT_DIR/scripts/stage8_evaluate.py" \
      --profile "$PROFILE" --require-screening-pass
    for tool in $(selected_tools); do
      run_index "$tool" 2
      run_index "$tool" 3
    done
    uv run --with pyyaml python "$ROOT_DIR/scripts/stage8_evaluate.py" \
      --profile "$PROFILE" --prepare-novel-judge
    ;;
esac
```

Stop immediately on a nonzero budget check or invalid run. Never auto-retry.

- [ ] **Step 5: Implement dry-run with no API calls**

Dry-run must print profile, exact knobs, output root, projected per-tool spend, reserve, and the commands for indices 1–3. It may run Stage 4 preflight but must not invoke an adapter.

- [ ] **Step 6: Add initial Stage 8 documentation**

Create the report with this factual pre-run skeleton and add its link to README:

```markdown
# Giai đoạn 8 — Tối ưu Pareto

> 🧪 **TRẠNG THÁI:** pipeline đã dựng, chưa gọi API cho profile `balanced-v1`.

## Baseline và gate

| Tool | Precision độc lập | Recall lesson | Stable TP |
|---|---:|---:|---:|
| arm-metis | 77.1% | 13/22 | 146 |
| datadog-saist | 89.1% | 5/22 | 28 |

Hard cap: $5.00; judge reserve: $0.25. Artifact nằm dưới
`results/optimization/balanced-v1/`; baseline Stage 4–7 không bị ghi đè.
```

README row: `| 8 | Nâng performance | 🧪 Pipeline sẵn sàng, chưa chạy | [docs/stage8-toi-uu-pareto.md](docs/stage8-toi-uu-pareto.md) |`.

- [ ] **Step 7: Run tests, dry-run, and syntax checks**

Run:

```bash
uv run python -m unittest -v tests.test_stage8_runner
bash -n scripts/stage8_run.sh scripts/stage4_run.sh adapters/*.sh
bash scripts/stage8_run.sh --dry-run
```

Expected: tests and syntax PASS; dry-run makes zero new proxy-log calls and creates no run directory.

- [ ] **Step 8: Record a diff checkpoint**

Run: `git diff --check -- scripts/stage8_run.sh tests/test_stage8_runner.py README.md docs/stage8-toi-uu-pareto.md`

Expected: exit 0; do not stage or commit.

---

### Task 6: Execute the real profile, judge novel findings, and publish the Pareto decision

**Files:**
- Generate: `results/optimization/balanced-v1/**`
- Modify: `docs/stage8-toi-uu-pareto.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: tested Stage 8 pipeline, Docker/SAIST image, Metis venv, live LiteLLM proxy, Gemini key.
- Produces: three-run resource statistics, independent quality statistics, budget ledger, and final per-tool Pareto decision.

- [ ] **Step 1: Run the complete local verification gate before spending**

Run:

```bash
uv run --with pyyaml python -m unittest discover -s tests -v
uv run --with pyyaml python -m compileall -q scripts proxy/token_logger.py
uv run --with pyyaml python -c "import yaml,pathlib; [yaml.safe_load(p.read_text(encoding='utf-8')) for p in [pathlib.Path('config/benchmark.yaml'),pathlib.Path('proxy/litellm_config.yaml')]]"
bash -n scripts/*.sh adapters/*.sh
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 2: Start required services and run preflight**

Start Docker Desktop if its engine is unavailable, recreate the proxy only if its current config/health requires it, then run:

```bash
bash scripts/stage8_run.sh --dry-run
```

Expected: target SHA, aliases, tools, output root, and budget projection all pass.

- [ ] **Step 3: Run screening**

Run: `bash scripts/stage8_run.sh --screen`

Expected: one valid run per tool under `results/optimization/balanced-v1/runs/`, followed by conservative screening JSON. If a tool fails its gate, stop that tool and document the measured failure.

- [ ] **Step 4: Complete only passing finalists**

Run: `bash scripts/stage8_run.sh --complete`

Expected: runs 2–3 execute only when the budget projection and screening gate pass. The command writes `novel.jsonl`; it does not submit a batch automatically.

- [ ] **Step 5: Judge novel finalist findings when `novel.jsonl` is nonempty**

Run:

```bash
uv run --with pyyaml python scripts/stage7_batch_judge.py --submit \
  --source results/optimization/balanced-v1/stats/novel.jsonl \
  --out results/optimization/balanced-v1/stats/judged-novel.jsonl \
  --state results/optimization/balanced-v1/stats/judge-state.json
```

Poll with the same three path arguments and `--status`. On success, use `--collect`. If `novel.jsonl` has zero lines, skip the batch and create an empty `judged-novel.jsonl`.

- [ ] **Step 6: Calculate the final decision**

Run:

```bash
uv run --with pyyaml python scripts/stage8_evaluate.py \
  --profile balanced-v1 --mode final
```

Expected: `final.json` reports actual spend ≤5.0, exact quality metrics, resource deltas, gate details, and `pareto_passed` per tool.

- [ ] **Step 7: Update documentation with measured facts**

Generate the final status from measured values rather than hand-editing tokens:

```python
if decision["pareto_passed"]:
    status = (f"> ✅ **PARETO IMPROVEMENT:** {tool} giảm "
              f"{best_metric} {abs(best_delta)*100:.1f}%; mọi quality gate qua.")
else:
    status = (f"> ❌ **KHÔNG QUA GATE:** {tool} thất bại tại {failed_gate}: "
              f"đo {actual}, ngưỡng {floor}; giữ baseline.")
```

All variables come from `final.json`; the report must not infer or manually
round a value differently from the artifact.

README must link the report and must not call Stage 8 complete unless `final.json` is internally consistent and every launched run is accounted for.

- [ ] **Step 8: Run final verification**

Run the full verification command from Step 1, then validate:

```bash
uv run --with pyyaml python -c "import json,pathlib; p=json.loads(pathlib.Path('results/optimization/balanced-v1/stats/final.json').read_text(encoding='utf-8')); assert p['actual_spend_usd'] <= 5.0; assert all('pareto_passed' in v for v in p['per_tool'].values()); print('STAGE8 RESULTS OK')"
```

Expected: all tests pass and `STAGE8 RESULTS OK` prints.

- [ ] **Step 9: Preserve the dirty worktree for user review**

Run: `git status --short` and report the Stage 8 files separately from pre-existing Stage 4–7 changes. Do not commit, push, reset, or clean without explicit user authorization.
