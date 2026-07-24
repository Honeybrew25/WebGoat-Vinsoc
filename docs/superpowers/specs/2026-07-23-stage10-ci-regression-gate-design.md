# Stage 10 CI Regression Gate Design

**Date:** 2026-07-23  
**Status:** Approved  
**Scope:** Offline, strict, reproducible regression checks for every pull request

## 1. Goal

Stage 10 turns the completed Stage 4–9 benchmark into a blocking CI contract.
Every pull request must prove that the repository still passes its tests, that
the Stage 9 decision package is current and internally consistent, and that
repository configuration and scripts remain parseable.

The same entry point must work locally and in GitHub Actions:

```bash
uv run --with pyyaml python scripts/stage10_ci_gate.py
```

The gate is read-only. It does not run scanners, start the proxy, call an API,
rerun a judge, regenerate a report, or mutate any Stage 4–9 artifact.

## 2. Success criteria

Stage 10 is complete when:

1. one Python entry point runs every required offline check;
2. all checks run even when an earlier check fails;
3. any failed, timed-out, missing, or crashed check makes the process exit `1`;
4. the same command works on Windows locally and Ubuntu in GitHub Actions;
5. GitHub Actions blocks pull requests on failure and exposes a readable Step
   Summary;
6. the gate writes no tracked repository file;
7. tests cover command execution, selection, reporting, workflow contracts, and
   the no-mutation guarantee;
8. the existing Stage 1–9 verification suite remains green.

## 3. Non-goals

Stage 10 does not:

- rerun Metis or SAIST;
- call the proxy, Gemini API, or either judge;
- optimize a profile or change a Pareto threshold;
- regenerate Stage 9 with `--write`;
- introduce a scheduled live benchmark;
- upload benchmark artifacts to an external service;
- create a new benchmark score or change the Stage 9 recommendation.

## 4. Architecture

### 4.1 Entry point

`scripts/stage10_ci_gate.py` is the only production entry point. It contains:

- a declarative registry of checks;
- a subprocess runner;
- result aggregation;
- console and Markdown rendering;
- CLI argument handling;
- exit-code selection.

The GitHub workflow contains no benchmark logic. It only prepares the runtime
and invokes the entry point.

### 4.2 Check model

Each registered check has:

- a unique stable ID;
- a user-facing name;
- an argv list, never a shell command string;
- a timeout;
- a working directory fixed to the repository root;
- a policy flag indicating whether the check is required.

Each completed check returns:

```json
{
  "id": "stage9-report",
  "status": "passed",
  "duration_seconds": 0.31,
  "message": "STAGE9 REPORT OK"
}
```

Allowed statuses are `passed` and `failed`. Timeout, executable-not-found, and
unexpected runner exceptions are normalized to `failed`; they do not escape as
an unhandled traceback.

### 4.3 Required checks

The default registry runs these checks:

| ID | Contract |
|---|---|
| `unit-tests` | `python -m unittest discover -s tests -v` |
| `python-compile` | `python -m compileall -q scripts proxy/token_logger.py` |
| `yaml` | parse `config/benchmark.yaml`, `proxy/litellm_config.yaml`, and the Stage 10 workflow with `yaml.safe_load` |
| `bash-syntax` | `bash -n scripts/*.sh adapters/*.sh`, with paths expanded by Python rather than a shell |
| `stage9-report` | `python scripts/stage9_report.py --check` in the same Python environment |
| `whitespace` | Git whitespace validation for the local working tree or the pull-request diff supplied by CI |

The runner uses `sys.executable` for Python child commands so the parent and
children use the same interpreter and installed dependencies.

The Bash check is required. A missing Bash executable is a clear failure with
installation guidance, not a skipped check.

### 4.4 Pull-request diff selection

The workflow fetches enough Git history to compare the pull request with its
base commit and passes that base revision to the gate. The whitespace check
runs `git diff --check <base>...HEAD`.

For a local run without an explicit base revision, the whitespace check runs
`git diff --check HEAD --` so both staged and unstaged changes are covered. The
CLI option is:

```bash
python scripts/stage10_ci_gate.py --base-ref <git-revision>
```

If `--base-ref` is omitted, the gate reads `STAGE10_BASE_REF`; an absent or
blank value means local working-tree mode. The GitHub workflow sets this
environment variable from the pull-request base SHA, allowing one identical
gate command for all event types.

The raw base revision is resolved first with
`git rev-parse --verify --end-of-options <revision>^{commit}`. The resulting
full commit SHA is passed as one argv item to `git diff`; the raw value is never
interpolated into a shell command or reused as a Git option.

## 5. CLI

### 5.1 Default

```bash
uv run --with pyyaml python scripts/stage10_ci_gate.py
```

Runs the full strict gate, prints all results, optionally writes a GitHub Step
Summary, and exits `0` only when every check passes.

### 5.2 List checks

```bash
uv run --with pyyaml python scripts/stage10_ci_gate.py --list
```

Prints stable IDs and descriptions without executing a check.

### 5.3 Select checks

```bash
uv run --with pyyaml python scripts/stage10_ci_gate.py \
  --only stage9-report,yaml
```

Selection preserves registry order. Empty selections, duplicate IDs, and
unknown IDs are usage errors and exit `2`.

`--only` is a diagnostic convenience. The GitHub workflow always runs the
default full registry.

### 5.4 GitHub summary destination

When `GITHUB_STEP_SUMMARY` is present, the gate appends its Markdown summary to
that exact path after validating that it identifies a writable regular file or
a not-yet-created file in an existing directory.

No summary file is created inside the repository unless the caller explicitly
points `GITHUB_STEP_SUMMARY` there. The checked-in workflow relies on the
GitHub-provided temporary path.

## 6. Execution and error handling

Checks run sequentially to keep logs deterministic and avoid subprocess output
interleaving. The gate does not fail fast.

For every child process:

- argv is passed without `shell=True`;
- stdout and stderr are captured as UTF-8 with replacement for invalid bytes;
- elapsed time uses a monotonic clock;
- the configured timeout is enforced;
- a nonzero return code is a failure;
- output retained in the final message is bounded, keeping the tail where test
  and compiler failures normally appear.

The console ends with a compact table and one final line:

```text
STAGE10 CI GATE PASSED: 6/6 checks
```

or:

```text
STAGE10 CI GATE FAILED: 2/6 checks failed
```

The failure summary includes each failed check ID, return code or normalized
failure type, and diagnostic output.

## 7. Read-only guarantee

The production gate must not call:

- `scripts/stage9_report.py --write`;
- any `stage4`, `stage7`, or `stage8` execution command;
- `git add`, `git commit`, `git checkout`, `git reset`, or another mutating Git
  command.

The no-mutation integration test snapshots path, size, modification time, and
SHA-256 for every existing file under these paths before and after a safe
selected gate run:

- `config/`;
- `results/`;
- `docs/stage9-bao-cao-cuoi.md`.

The test excludes interpreter caches and temporary test directories. The
default CI run separately validates the entire registry.

## 8. GitHub Actions workflow

`.github/workflows/benchmark-regression.yml` has:

- triggers for `pull_request`, pushes to `main`, and `workflow_dispatch`;
- `permissions: contents: read`;
- an Ubuntu runner;
- a 10-minute job timeout;
- concurrency keyed by workflow and pull request or branch, with
  `cancel-in-progress: true`;
- checkout with sufficient history for the PR merge-base;
- an official `uv` setup action pinned to the current supported release during
  implementation;
- Python 3.12 installed and selected through `uv`;
- one gate step that supplies the pull-request base SHA when applicable;
- no secrets and no write permissions.

Push and manual runs omit `--base-ref` unless the workflow has a trustworthy
base revision. They still run all content checks and local `git diff --check`.

## 9. Testing

`tests/test_stage10_ci_gate.py` covers:

### 9.1 Pure behavior

- registry IDs are unique and ordered;
- `--only` preserves registry order;
- unknown, duplicate, and empty selections are rejected;
- exit code is `0` only when all selected checks pass;
- console and Markdown summaries are deterministic.

### 9.2 Runner behavior

- successful command;
- nonzero command;
- timeout;
- missing executable;
- unexpected runner exception;
- UTF-8 replacement and bounded diagnostic output;
- argv execution without a shell.

### 9.3 Integration and contracts

- a selected real Stage 9/YAML check passes;
- a deliberately stale Stage 9 output causes failure without being repaired;
- a real safe gate run does not mutate Stage 4–9 artifacts;
- workflow YAML parses;
- workflow triggers, permissions, timeout, concurrency, Python version, and
  strict full-gate command match this design;
- workflow contains no scanner, proxy, API, judge, or report-write command;
- README and Stage 0 documentation link the completed Stage 10 guide.

Tests must not recursively launch `unit-tests` from within the full unittest
suite. Integration tests use `--only` or call the registry with a controlled
runner.

## 10. Documentation

Add `docs/stage10-ci-regression.md` with:

- the purpose and strict blocking policy;
- the local default, `--list`, `--only`, and `--base-ref` commands;
- the meaning of each check;
- common failure diagnosis;
- proof that the gate is offline and read-only;
- the GitHub trigger and permission model.

Update:

- `README.md` from “Lộ trình 9 giai đoạn” to “Lộ trình 10 giai đoạn” and add a
  completed Stage 10 row;
- `docs/00-tong-quan.md` with a Stage 10 entry after the final-report stage.

Stage 9 metrics and recommendations remain unchanged.

## 11. Verification

Before completion, run fresh:

```bash
uv run --with pyyaml python -m unittest discover -s tests -v
uv run --with pyyaml python scripts/stage10_ci_gate.py
uv run --with pyyaml python scripts/stage9_report.py --check
uv run --with pyyaml python -m compileall -q scripts proxy/token_logger.py
bash -n scripts/*.sh adapters/*.sh
git diff --check
```

Also parse every YAML file used by the gate and inspect `git status --short` to
confirm that running Stage 10 created or changed no artifact.

## 12. Delivery constraints

- Work directly on the current dirty `main` tree approved for the preceding
  stages.
- Preserve all existing staged, modified, and untracked Stage 4–9 work.
- Do not commit, stage, push, reset, clean, or create a pull request.
- Do not call an external API or perform a live scanner/judge run.
- Treat the existing Stage 9 summary and report as immutable inputs.
