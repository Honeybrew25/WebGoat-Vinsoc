# Stage 9 Final Report Design

**Date:** 2026-07-23  
**Status:** Approved in conversation; awaiting written-spec review  
**Scope:** Deterministic final report generation from completed Stage 4–8 artifacts

## Goal

Stage 9 turns the benchmark artifacts into one reproducible decision package:

- a concise executive conclusion for decision-makers;
- a technical report with traceable metrics and caveats;
- a machine-readable summary that is the sole data source for the Markdown;
- scenario-specific recommendations instead of an artificial universal winner.

The final recommendation is:

- use optimized Metis for deep review where coverage matters most;
- use optimized SAIST for per-PR CI where precision and latency matter most;
- combine SAIST per PR with Metis nightly or before release.

Stage 9 does not call an API, rerun a scanner, rerun a judge, or mutate any
Stage 4–8 artifact.

## Outputs

The independent generator is `scripts/stage9_report.py`.

It produces:

- `results/report/final-summary.json`
- `docs/stage9-bao-cao-cuoi.md`

It also updates the Stage 9 row in `README.md` during implementation. The
generated report itself is never hand-maintained: `final-summary.json` is built
first, and the Markdown is rendered only from that in-memory summary.

## CLI

```bash
uv run --with pyyaml python scripts/stage9_report.py --write
uv run --with pyyaml python scripts/stage9_report.py --check
```

`--write` validates all inputs and atomically writes both outputs.

`--check` generates the expected JSON and Markdown in memory, validates the
source artifacts, and exits nonzero if either checked-in/generated output is
missing or stale. It performs no writes.

The modes are mutually exclusive and one mode is required.

## Source Artifacts

The generator reads these repository-confined sources:

- `config/benchmark.yaml`
- `results/stats/cost_by_run.json`
- `results/stats/counts.json`
- `results/stats/precision.json`
- `results/stats/precision-independent.json`
- `results/stats/recall.json`
- `results/stats/judge_agreement.json`
- `results/findings/normalized/judged.jsonl`
- `results/findings/normalized/judged-independent.jsonl`
- `results/optimization/<active_profile>/profile.json`
- `results/optimization/<active_profile>/stats/cost_by_run.json`
- `results/optimization/<active_profile>/stats/final.json`
- `results/optimization/<active_profile>/stats/judged-final.jsonl`
- `results/optimization/<active_profile>/stats/judged-novel.jsonl`
- `results/optimization/<active_profile>/stats/judge-state.json`

The active Stage 8 profile comes from `config/benchmark.yaml`; it is not
hard-coded in report logic.

## Architecture and Data Flow

The generator has four bounded layers:

1. **Loaders** parse YAML, JSON, and JSONL and retain each input path in error
   messages.
2. **Validators** establish that the benchmark and Stage 8 results are complete
   and internally consistent.
3. **Aggregator** recomputes derived metrics and builds a versioned Python
   dictionary representing the final summary.
4. **Renderers** serialize canonical JSON and render Markdown only from the
   summary dictionary.

Data flows in one direction:

```text
Stage 4–8 artifacts
        |
        v
 load -> validate -> aggregate -> final-summary.json
                                -> stage9-bao-cao-cuoi.md
```

No renderer reads source artifacts directly. This prevents the JSON and
Markdown conclusions from drifting apart.

## Machine-Readable Summary

`final-summary.json` has `schema_version: 1` and these top-level sections:

- `benchmark`: target SHA, target identity, model, temperature, scope, repeat
  policy, and active optimized profile;
- `findings`: baseline unique/stable counts and cross-tool-match count from the
  normalized Stage 6 result;
- `quality`: final independent TP/FP, precision, lesson recall, stable TP, and
  approximate lesson-level F1 per tool;
- `judge_sensitivity`: baseline same-model and independent precision,
  agreement, Cohen's kappa, and per-tool precision deltas;
- `resources`: baseline medians, optimized medians, and relative deltas for
  wall clock, total tokens, and cost per run;
- `budget`: scan spend, independent-judge spend, total Stage 8 spend, cap, and
  pass/fail;
- `blind_spots`: expected vulnerable lessons not hit with the expected CWE by
  either tool in the final independent verdict set;
- `recommendations`: coverage-review choice, CI-gate choice, and combined
  operating model with machine-readable reasons;
- `limitations`: fixed method caveats;
- `provenance`: repository-relative path and SHA-256 for every source artifact.

Keys are serialized in sorted order with UTF-8, two-space indentation, LF line
endings, and one trailing newline. No timestamp, hostname, absolute path, API
key, or other volatile value appears in the output.

## Derived Metrics

### Final Quality

Primary quality metrics come from the optimized Stage 8 independent verdicts,
not the older same-model judge:

```text
recall_fraction = recall_lessons / vulnerable_lesson_denominator
approximate_f1 = 2 * precision * recall_fraction
                 / (precision + recall_fraction)
```

F1 is explicitly labeled approximate because recall is measured at lesson
granularity rather than finding or line granularity.

The generator recomputes TP, FP, precision, stable TP, strict lesson hits, and
F1 from `judged-final.jsonl`. It compares these values with `final.json`.

### Resource Medians

For each tool and resource:

```text
baseline = median(Stage 4 run values)
optimized = median(Stage 8 run values)
relative_delta = (optimized - baseline) / baseline
```

Total tokens are recomputed as input plus output tokens for baseline runs and
read from the normalized Stage 8 resource rows.

### Blind Spots

For each non-null lesson/CWE pair in `benchmark.yaml`, a tool hits the lesson
only when a final TP inside `lessons/<lesson>/` has a judge CWE equal to the
expected CWE. A common blind spot is a vulnerable lesson hit by neither tool.

The report does not copy the old Stage 7b blind-spot list because Stage 8 has a
new final verdict set.

## Recommendation Rules

Only tools with `pareto_passed: true` are eligible.

### Deep Security Review

Rank eligible tools by:

1. strict lesson recall, descending;
2. TP count, descending;
3. approximate F1, descending;
4. tool ID, ascending for a deterministic final tie-break.

This selects Metis for the measured benchmark.

### Per-PR CI Gate

Rank eligible tools by:

1. independent precision, descending;
2. median wall-clock time, ascending;
3. median cost per run, ascending;
4. tool ID, ascending.

This selects SAIST for the measured benchmark. The narrative may state that
SAIST is also faster and cheaper only when those comparisons are true in the
summary; it must not assume them from the tool name.

### Combined Operating Model

If the two scenario winners differ, recommend the CI winner on every PR and
the coverage winner nightly or before release. If the same tool wins both,
recommend that tool for both schedules and state that no second scanner is
selected by the measured criteria.

No weighted composite score and no universal winner are emitted.

## Markdown Structure

`docs/stage9-bao-cao-cuoi.md` has two reading layers.

The opening executive layer contains:

1. benchmark answer in three sentences;
2. scenario recommendation cards expressed as compact blockquotes;
3. one quality comparison table;
4. one resource comparison table;
5. total measured spend.

The technical layer contains:

1. benchmark identity and fairness controls;
2. final optimized quality and approximate F1;
3. baseline-to-optimized resource deltas;
4. judge sensitivity with both precision sets and kappa;
5. final common blind spots;
6. deployment recommendation and rationale;
7. methodological limitations;
8. reproduction commands;
9. source-artifact manifest with SHA-256 digests.

The report states that judge-sensitivity metrics were measured on the Stage 7
baseline set. It does not imply that same-model precision was recomputed for
the Stage 8 final findings.

## Validation and Failure Behavior

Generation fails before writing either output when any condition below is
false:

- every required source exists and parses;
- target SHA, model, scope, and active profile agree across config, profile,
  run rows, and final decision;
- baseline and optimized datasets each contain exactly three valid runs per
  enabled Stage 8 tool;
- Stage 6 count rows exist for every eligible tool and their stable counts do
  not exceed their unique counts;
- each final scoped finding has a TP or FP verdict;
- recomputed judged count equals TP plus FP;
- recomputed precision, stable TP, strict lesson recall, medians, resource
  deltas, and spend match exact Stage 8 summary fields within a floating
  tolerance of `1e-9`; legacy Stage 7 fields stored to four decimal places
  match after `round(value, 4)`;
- the baseline same-model and independent sets each contain exactly 238 valid
  aligned verdicts;
- the novel batch is collected, its successful count matches the novel
  verdict count, and usage accounting is complete;
- Stage 8 spend does not exceed its configured cap;
- every reported finalist passed both quality and resource gates and has
  `pareto_passed: true`.

Errors identify the artifact, tool, field, expected value, and actual value
when applicable. Validation never silently substitutes a default metric.

`--write` renders both outputs fully before writing. Each file uses a sibling
temporary file followed by `os.replace`; a rendering or validation error leaves
existing outputs untouched.

## Testing

`tests/test_stage9_report.py` covers:

- approximate F1, including zero-denominator behavior;
- recommendation selection and deterministic tie-breaks;
- strict lesson-hit and final common-blind-spot calculation;
- baseline and optimized median/resource delta calculation;
- rejection of missing, malformed, or contradictory artifacts;
- rejection of incomplete judge usage and failed Pareto gates;
- canonical JSON and deterministic Markdown rendering;
- `--check` stale-output detection;
- an integration test against the real Stage 4–8 artifact set.

Verification also runs:

```bash
uv run --with pyyaml python -m unittest discover -s tests -v
uv run --with pyyaml python -m compileall -q scripts proxy/token_logger.py
uv run --with pyyaml python scripts/stage9_report.py --check
bash -n scripts/*.sh adapters/*.sh
git diff --check
```

The integration assertion requires two scenario recommendations, six
optimized run rows, complete provenance, spend within the configured cap, and
no stale generated output.

## Documentation Changes

- Add and generate `docs/stage9-bao-cao-cuoi.md`.
- Change the README Stage 9 row to complete and link the report.
- Update the Stage 0 roadmap text so Stage 8 and Stage 9 are no longer described
  as future work.

## Out of Scope

- New scanner or judge calls.
- Rejudging baseline findings.
- HTML dashboards, PDF export, charts, or a web application.
- A weighted score or a universal winner.
- Generalizing the generator to unrelated benchmark repositories.
- Changing Stage 8 decisions or optimization gates.

## Repository State

The current `main` worktree contains staged and untracked Stage 4–8 work. Stage
9 implementation must preserve that state and must not commit, push, reset,
clean, or alter the existing staged index without explicit user authorization.
