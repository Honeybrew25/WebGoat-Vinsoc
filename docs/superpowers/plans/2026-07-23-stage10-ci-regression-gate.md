# Stage 10 CI Regression Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one read-only, offline Python gate that runs the repository's regression contracts locally and blocks GitHub pull requests when any contract fails.

**Architecture:** `scripts/stage10_ci_gate.py` owns a declarative check registry, safe subprocess execution, deterministic aggregation, CLI selection, and console/GitHub Markdown reporting. A thin GitHub Actions workflow prepares Python 3.12 and `uv`, then invokes the same default gate command used locally; tests isolate command execution and use selected real checks to prove read-only behavior without recursive test launches.

**Tech Stack:** Python 3.12+, standard library (`argparse`, `dataclasses`, `pathlib`, `subprocess`, `time`), PyYAML supplied through `uv --with pyyaml`, Git, Bash, GitHub Actions.

## Global Constraints

- The production command is `uv run --with pyyaml python scripts/stage10_ci_gate.py`.
- Run offline only: no scanner, proxy, API, judge, network upload, or live benchmark call.
- Never invoke `scripts/stage9_report.py --write`; Stage 9 is an immutable input.
- Do not write a tracked output or mutate anything under `config/`, `results/`, or the Stage 9 report.
- Run every selected check and aggregate all failures; do not fail fast.
- Use argv arrays and `shell=False` for every subprocess.
- A timeout, missing executable, nonzero exit, or runner exception is a failed check.
- GitHub uses Python `3.12`, a 10-minute job timeout, read-only permissions, and cancel-in-progress concurrency.
- Pin `actions/checkout` to `de0fac2e4500dabe0009e67214ff5f5447ce83dd` (`v6.0.2`).
- Pin `astral-sh/setup-uv` to `08807647e7069bb48b6ef5acd8ec9567f424441b` (`v8.1.0`) and uv to `0.11.31`.
- Work in the current dirty `main` tree; preserve all existing Stage 4–9 changes.
- Do not commit, stage, push, reset, clean, or create a pull request. Task checkpoints are test-and-review checkpoints, not commits.

---

## File map

- Create `scripts/stage10_ci_gate.py`: check definitions, runner, aggregation, renderers, CLI, hidden YAML subprocess check.
- Create `tests/test_stage10_ci_gate.py`: unit, integration, workflow, read-only, and documentation contracts.
- Create `.github/workflows/benchmark-regression.yml`: thin, read-only GitHub Actions adapter.
- Create `docs/stage10-ci-regression.md`: operator guide and failure diagnosis.
- Modify `README.md`: ten-stage roadmap, Stage 10 row, and local gate command.
- Modify `docs/00-tong-quan.md`: Stage 10 overview link.

---

### Task 1: Safe check model, selection, and subprocess runner

**Files:**
- Create: `scripts/stage10_ci_gate.py`
- Create: `tests/test_stage10_ci_gate.py`

**Interfaces:**
- Consumes: repository root as `pathlib.Path`; subprocess argv as `tuple[str, ...]`.
- Produces:
  - `CheckSpec(id: str, name: str, argv: tuple[str, ...], timeout_seconds: float)`
  - `CheckResult(id: str, name: str, status: str, duration_seconds: float, message: str, return_code: int | None)`
  - `select_checks(registry: tuple[CheckSpec, ...], raw: str | None) -> tuple[CheckSpec, ...]`
  - `run_check(spec: CheckSpec, root: pathlib.Path, *, run=subprocess.run, clock=time.monotonic) -> CheckResult`

- [ ] **Step 1: Add import helper and failing model/selection tests**

Create the beginning of `tests/test_stage10_ci_gate.py`:

```python
import importlib.util
import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "stage10_ci_gate.py"
SPEC = importlib.util.spec_from_file_location("stage10_ci_gate", MODULE_PATH)
GATE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = GATE
SPEC.loader.exec_module(GATE)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.registry = (
            GATE.CheckSpec("alpha", "Alpha", ("alpha",), 1.0),
            GATE.CheckSpec("beta", "Beta", ("beta",), 2.0),
            GATE.CheckSpec("gamma", "Gamma", ("gamma",), 3.0),
        )

    def test_selection_preserves_registry_order(self):
        selected = GATE.select_checks(self.registry, "gamma,alpha")
        self.assertEqual(["alpha", "gamma"], [item.id for item in selected])

    def test_selection_rejects_empty_duplicate_and_unknown_ids(self):
        for raw in ("", "alpha,alpha", "missing"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                GATE.select_checks(self.registry, raw)
```

- [ ] **Step 2: Run the selection tests and confirm RED**

Run:

```powershell
uv run --with pyyaml python -m unittest -v tests.test_stage10_ci_gate.SelectionTests
```

Expected: import failure because `scripts/stage10_ci_gate.py` does not exist or
`CheckSpec` is undefined.

- [ ] **Step 3: Implement the immutable models and selection**

Create `scripts/stage10_ci_gate.py` with:

```python
#!/usr/bin/env python3
"""Run the read-only Stage 10 CI regression gate."""

from __future__ import annotations

import argparse
import dataclasses
import os
import pathlib
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence


_ROOT = pathlib.Path(__file__).resolve().parents[1]
_MAX_DIAGNOSTIC_CHARS = 20_000
_VALID_STATUSES = frozenset({"passed", "failed"})


@dataclasses.dataclass(frozen=True)
class CheckSpec:
    id: str
    name: str
    argv: tuple[str, ...]
    timeout_seconds: float

    def __post_init__(self) -> None:
        if not self.id or "," in self.id:
            raise ValueError(f"invalid check id: {self.id!r}")
        if not self.argv:
            raise ValueError(f"{self.id}: argv cannot be empty")
        if self.timeout_seconds <= 0:
            raise ValueError(f"{self.id}: timeout must be positive")


@dataclasses.dataclass(frozen=True)
class CheckResult:
    id: str
    name: str
    status: str
    duration_seconds: float
    message: str
    return_code: int | None

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"invalid result status: {self.status!r}")
        if self.duration_seconds < 0:
            raise ValueError("duration cannot be negative")


def select_checks(
    registry: tuple[CheckSpec, ...], raw: str | None
) -> tuple[CheckSpec, ...]:
    ids = [item.id for item in registry]
    if len(ids) != len(set(ids)):
        raise ValueError("check registry contains duplicate ids")
    if raw is None:
        return registry
    requested = raw.split(",")
    if not requested or any(not item for item in requested):
        raise ValueError("--only requires a comma-separated non-empty id list")
    if len(requested) != len(set(requested)):
        raise ValueError("--only contains duplicate check ids")
    unknown = sorted(set(requested) - set(ids))
    if unknown:
        raise ValueError(f"unknown check ids: {', '.join(unknown)}")
    wanted = set(requested)
    return tuple(item for item in registry if item.id in wanted)
```

- [ ] **Step 4: Run the selection tests and confirm GREEN**

Run the command from Step 2.

Expected: `Ran 2 tests` and `OK`.

- [ ] **Step 5: Add failing runner behavior tests**

Append:

```python
class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.spec = GATE.CheckSpec("sample", "Sample", ("tool", "arg"), 5.0)

    @staticmethod
    def clock():
        values = iter((10.0, 10.25))
        return lambda: next(values)

    def test_success_and_nonzero_are_normalized(self):
        for code, status in ((0, "passed"), (7, "failed")):
            with self.subTest(code=code):
                calls = []

                def fake_run(argv, **kwargs):
                    calls.append((argv, kwargs))
                    return types.SimpleNamespace(
                        returncode=code, stdout="stdout\n", stderr="stderr\n"
                    )

                result = GATE.run_check(
                    self.spec,
                    ROOT,
                    run=fake_run,
                    clock=self.clock(),
                )
                self.assertEqual(status, result.status)
                self.assertEqual(code, result.return_code)
                self.assertEqual(("tool", "arg"), calls[0][0])
                self.assertFalse(calls[0][1]["shell"])
                self.assertEqual(0.25, result.duration_seconds)

    def test_timeout_missing_executable_and_exception_are_failed(self):
        errors = (
            subprocess.TimeoutExpired(("tool",), 5, output="partial"),
            FileNotFoundError("missing"),
            RuntimeError("boom"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                def fake_run(*args, **kwargs):
                    raise error

                result = GATE.run_check(
                    self.spec,
                    ROOT,
                    run=fake_run,
                    clock=self.clock(),
                )
                self.assertEqual("failed", result.status)
                self.assertIsNone(result.return_code)
                self.assertIn(type(error).__name__, result.message)

    def test_diagnostic_output_is_tail_bounded(self):
        marker = "TAIL-MARKER"

        def fake_run(*args, **kwargs):
            return types.SimpleNamespace(
                returncode=1,
                stdout="x" * (GATE._MAX_DIAGNOSTIC_CHARS + 100),
                stderr=marker,
            )

        result = GATE.run_check(
            self.spec, ROOT, run=fake_run, clock=self.clock()
        )
        self.assertLessEqual(len(result.message), GATE._MAX_DIAGNOSTIC_CHARS)
        self.assertTrue(result.message.endswith(marker))
```

Add `import subprocess` to the test imports.

- [ ] **Step 6: Run runner tests and confirm RED**

Run:

```powershell
uv run --with pyyaml python -m unittest -v tests.test_stage10_ci_gate.RunnerTests
```

Expected: FAIL because `run_check` is undefined.

- [ ] **Step 7: Implement safe subprocess execution**

Append to the production script:

```python
def _bounded_message(stdout: object, stderr: object) -> str:
    parts = []
    for value in (stdout, stderr):
        if value is None:
            continue
        if isinstance(value, bytes):
            text = value.decode("utf-8", errors="replace")
        else:
            text = str(value)
        if text:
            parts.append(text)
    message = "".join(parts).rstrip()
    if not message:
        return "(no command output)"
    return message[-_MAX_DIAGNOSTIC_CHARS:]


def run_check(
    spec: CheckSpec,
    root: pathlib.Path,
    *,
    run: Callable[..., object] = subprocess.run,
    clock: Callable[[], float] = time.monotonic,
) -> CheckResult:
    started = clock()
    try:
        completed = run(
            spec.argv,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=spec.timeout_seconds,
            check=False,
            shell=False,
        )
        return_code = int(completed.returncode)
        status = "passed" if return_code == 0 else "failed"
        message = _bounded_message(completed.stdout, completed.stderr)
    except subprocess.TimeoutExpired as exc:
        return_code = None
        status = "failed"
        detail = _bounded_message(exc.output, exc.stderr)
        message = f"TimeoutExpired after {spec.timeout_seconds:.0f}s: {detail}"
    except FileNotFoundError as exc:
        return_code = None
        status = "failed"
        message = f"FileNotFoundError: {exc}"
    except Exception as exc:  # Normalize runner failures into gate results.
        return_code = None
        status = "failed"
        message = f"{type(exc).__name__}: {exc}"
    elapsed = max(0.0, clock() - started)
    return CheckResult(
        id=spec.id,
        name=spec.name,
        status=status,
        duration_seconds=elapsed,
        message=message,
        return_code=return_code,
    )
```

- [ ] **Step 8: Run Task 1 tests and checkpoint**

Run:

```powershell
uv run --with pyyaml python -m unittest -v \
  tests.test_stage10_ci_gate.SelectionTests \
  tests.test_stage10_ci_gate.RunnerTests
```

Expected: `Ran 5 tests` and `OK`. Do not stage or commit. Record the passing
command and hand Task 1 to a fresh reviewer.

---

### Task 2: Registry, aggregation, renderers, and CLI

**Files:**
- Modify: `scripts/stage10_ci_gate.py`
- Modify: `tests/test_stage10_ci_gate.py`

**Interfaces:**
- Consumes: Task 1 models and `run_check`.
- Produces:
  - `build_registry(root: pathlib.Path, python: str, base_ref: str | None, bash: str = "bash", git: str = "git") -> tuple[CheckSpec, ...]`
  - `resolve_base_ref(root: pathlib.Path, raw: str, *, git: str = "git") -> str`
  - `run_gate(specs: tuple[CheckSpec, ...], root: pathlib.Path, *, execute=run_check) -> tuple[CheckResult, ...]`
  - `render_console(results: Sequence[CheckResult]) -> str`
  - `render_markdown(results: Sequence[CheckResult]) -> str`
  - `append_github_summary(raw_path: str, markdown: str) -> None`
  - `main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int`

- [ ] **Step 1: Add failing registry tests**

Append:

```python
class RegistryTests(unittest.TestCase):
    def test_registry_is_exact_ordered_and_shell_free(self):
        registry = GATE.build_registry(
            ROOT, "python-bin", "base-sha", bash="bash-bin", git="git-bin"
        )
        self.assertEqual(
            [
                "unit-tests",
                "python-compile",
                "yaml",
                "bash-syntax",
                "stage9-report",
                "whitespace",
            ],
            [item.id for item in registry],
        )
        by_id = {item.id: item for item in registry}
        self.assertEqual("python-bin", by_id["unit-tests"].argv[0])
        self.assertEqual(
            ("git-bin", "diff", "--check", "base-sha...HEAD", "--"),
            by_id["whitespace"].argv,
        )
        self.assertTrue(
            all(path.endswith(".sh") for path in by_id["bash-syntax"].argv[2:])
        )

    def test_local_whitespace_check_has_no_base_range(self):
        registry = GATE.build_registry(ROOT, "python", None)
        whitespace = next(item for item in registry if item.id == "whitespace")
        self.assertEqual(
            ("git", "diff", "--check", "HEAD", "--"),
            whitespace.argv,
        )

    def test_base_ref_resolution_rejects_option_like_or_invalid_values(self):
        for raw in ("--help", "definitely-not-a-revision"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                GATE.resolve_base_ref(ROOT, raw)
```

- [ ] **Step 2: Run registry tests and confirm RED**

Run:

```powershell
uv run --with pyyaml python -m unittest -v tests.test_stage10_ci_gate.RegistryTests
```

Expected: FAIL because `build_registry` is undefined.

- [ ] **Step 3: Implement the exact registry and hidden YAML check**

Append:

```python
def _relative_strings(root: pathlib.Path, paths: Sequence[pathlib.Path]) -> tuple[str, ...]:
    return tuple(path.relative_to(root).as_posix() for path in sorted(paths))


def build_registry(
    root: pathlib.Path,
    python: str,
    base_ref: str | None,
    *,
    bash: str = "bash",
    git: str = "git",
) -> tuple[CheckSpec, ...]:
    shell_files = _relative_strings(
        root,
        tuple((root / "scripts").glob("*.sh"))
        + tuple((root / "adapters").glob("*.sh")),
    )
    if not shell_files:
        raise ValueError("no Bash scripts found under scripts/ or adapters/")
    script = pathlib.Path(__file__).resolve()
    whitespace = (git, "diff", "--check", "HEAD", "--")
    if base_ref:
        whitespace = (git, "diff", "--check", f"{base_ref}...HEAD", "--")
    return (
        CheckSpec(
            "unit-tests",
            "Unit tests",
            (python, "-m", "unittest", "discover", "-s", "tests", "-v"),
            300.0,
        ),
        CheckSpec(
            "python-compile",
            "Python compile",
            (
                python,
                "-m",
                "compileall",
                "-q",
                "scripts",
                "proxy/token_logger.py",
            ),
            60.0,
        ),
        CheckSpec(
            "yaml",
            "YAML parse",
            (python, str(script), "--internal-yaml-check"),
            60.0,
        ),
        CheckSpec(
            "bash-syntax",
            "Bash syntax",
            (bash, "-n", *shell_files),
            60.0,
        ),
        CheckSpec(
            "stage9-report",
            "Stage 9 report",
            (python, "scripts/stage9_report.py", "--check"),
            60.0,
        ),
        CheckSpec("whitespace", "Git whitespace", whitespace, 60.0),
    )


def resolve_base_ref(
    root: pathlib.Path, raw: str, *, git: str = "git"
) -> str:
    if not raw or raw.startswith("-"):
        raise ValueError(f"invalid base revision: {raw!r}")
    completed = subprocess.run(
        (
            git,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{raw}^{{commit}}",
        ),
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        shell=False,
    )
    resolved = completed.stdout.strip()
    if completed.returncode != 0 or len(resolved) != 40:
        detail = _bounded_message(completed.stdout, completed.stderr)
        raise ValueError(f"invalid base revision {raw!r}: {detail}")
    return resolved


def _internal_yaml_check(root: pathlib.Path) -> int:
    import yaml

    paths = (
        root / "config" / "benchmark.yaml",
        root / "proxy" / "litellm_config.yaml",
        root / ".github" / "workflows" / "benchmark-regression.yml",
    )
    for path in paths:
        if not path.is_file():
            raise ValueError(f"missing YAML file: {path.relative_to(root)}")
        with path.open("r", encoding="utf-8") as handle:
            yaml.safe_load(handle)
    print("STAGE10 YAML OK")
    return 0
```

- [ ] **Step 4: Run registry tests and confirm GREEN**

Run the Step 2 command.

Expected: `Ran 3 tests` and `OK`.

- [ ] **Step 5: Add failing aggregation and renderer tests**

Append:

```python
class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.results = (
            GATE.CheckResult("alpha", "Alpha", "passed", 0.1, "ok", 0),
            GATE.CheckResult("beta", "Beta", "failed", 0.2, "broken", 3),
        )

    def test_run_gate_does_not_fail_fast(self):
        specs = (
            GATE.CheckSpec("alpha", "Alpha", ("a",), 1),
            GATE.CheckSpec("beta", "Beta", ("b",), 1),
        )
        seen = []

        def execute(spec, root):
            seen.append(spec.id)
            return self.results[len(seen) - 1]

        self.assertEqual(
            self.results, GATE.run_gate(specs, ROOT, execute=execute)
        )
        self.assertEqual(["alpha", "beta"], seen)

    def test_console_and_markdown_report_all_failures(self):
        console = GATE.render_console(self.results)
        markdown = GATE.render_markdown(self.results)
        self.assertEqual(console, GATE.render_console(self.results))
        self.assertEqual(markdown, GATE.render_markdown(self.results))
        self.assertIn("STAGE10 CI GATE FAILED: 1/2 checks failed", console)
        self.assertIn("beta", console)
        self.assertIn("| beta | FAIL |", markdown)
        self.assertIn("broken", markdown)
```

- [ ] **Step 6: Run reporting tests and confirm RED**

Run:

```powershell
uv run --with pyyaml python -m unittest -v tests.test_stage10_ci_gate.ReportingTests
```

Expected: FAIL because aggregation/rendering functions are undefined.

- [ ] **Step 7: Implement aggregation and deterministic renderers**

Append:

```python
def run_gate(
    specs: tuple[CheckSpec, ...],
    root: pathlib.Path,
    *,
    execute: Callable[[CheckSpec, pathlib.Path], CheckResult] = run_check,
) -> tuple[CheckResult, ...]:
    return tuple(execute(spec, root) for spec in specs)


def _label(status: str) -> str:
    return "PASS" if status == "passed" else "FAIL"


def render_console(results: Sequence[CheckResult]) -> str:
    failed = [item for item in results if item.status == "failed"]
    lines = ["ID                 STATUS  SECONDS", "-----------------  ------  -------"]
    for item in results:
        lines.append(
            f"{item.id:<17}  {_label(item.status):<6}  "
            f"{item.duration_seconds:>7.3f}"
        )
    if failed:
        lines.append("")
        for item in failed:
            lines.extend((f"[{item.id}]", item.message))
        lines.extend((
            "",
            f"STAGE10 CI GATE FAILED: {len(failed)}/{len(results)} checks failed",
        ))
    else:
        lines.extend((
            "",
            f"STAGE10 CI GATE PASSED: {len(results)}/{len(results)} checks",
        ))
    return "\n".join(lines) + "\n"


def render_markdown(results: Sequence[CheckResult]) -> str:
    failed = [item for item in results if item.status == "failed"]
    lines = [
        "## Stage 10 CI regression gate",
        "",
        "| Check | Status | Seconds |",
        "|---|---:|---:|",
    ]
    for item in results:
        lines.append(
            f"| {item.id} | {_label(item.status)} | "
            f"{item.duration_seconds:.3f} |"
        )
    if failed:
        lines.extend(("", "### Failures", ""))
        for item in failed:
            safe = item.message.replace("```", "'''")
            lines.extend((f"#### `{item.id}`", "", "```text", safe, "```", ""))
    else:
        lines.extend(("", f"**PASSED: {len(results)}/{len(results)} checks.**", ""))
    return "\n".join(lines).rstrip() + "\n"
```

- [ ] **Step 8: Run reporting tests and confirm GREEN**

Run the Step 6 command.

Expected: `Ran 2 tests` and `OK`.

- [ ] **Step 9: Add failing summary-path and CLI tests**

Append:

```python
import io
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock


class CliTests(unittest.TestCase):
    def test_list_and_unknown_selection_exit_codes(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = GATE.main(["--list"], {})
        self.assertEqual(0, code)
        self.assertIn("stage9-report", stdout.getvalue())

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = GATE.main(["--only", "missing"], {})
        self.assertEqual(2, code)
        self.assertIn("unknown check ids", stderr.getvalue())

    def test_environment_base_ref_is_used_when_cli_value_is_absent(self):
        with (
            mock.patch.object(
                GATE,
                "resolve_base_ref",
                return_value="a" * 40,
            ) as resolve,
            mock.patch.object(GATE, "build_registry") as build,
        ):
            build.return_value = ()
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    0, GATE.main([], {"STAGE10_BASE_REF": "base-sha"})
                )
        resolve.assert_called_once_with(GATE._ROOT, "base-sha")
        self.assertEqual("a" * 40, build.call_args.args[2])

    def test_summary_path_must_have_existing_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            summary = root / "summary.md"
            GATE.append_github_summary(str(summary), "summary\n")
            self.assertEqual(b"summary\n", summary.read_bytes())
            with self.assertRaises(ValueError):
                GATE.append_github_summary(str(root), "summary\n")
            missing = root / "missing" / "summary.md"
            with self.assertRaises(ValueError):
                GATE.append_github_summary(str(missing), "summary\n")
```

- [ ] **Step 10: Run CLI tests and confirm RED**

Run:

```powershell
uv run --with pyyaml python -m unittest -v tests.test_stage10_ci_gate.CliTests
```

Expected: FAIL because `main` and `append_github_summary` are undefined.

- [ ] **Step 11: Implement summary writing and CLI**

Append:

```python
def append_github_summary(raw_path: str, markdown: str) -> None:
    path = pathlib.Path(raw_path)
    if path.is_symlink():
        raise ValueError("GITHUB_STEP_SUMMARY cannot be a symbolic link")
    if path.exists() and not path.is_file():
        raise ValueError("GITHUB_STEP_SUMMARY is not a regular file")
    if not path.parent.is_dir():
        raise ValueError("GITHUB_STEP_SUMMARY parent directory does not exist")
    try:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(markdown)
    except OSError as exc:
        raise ValueError(f"cannot write GITHUB_STEP_SUMMARY: {exc}") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the offline, read-only Stage 10 CI gate."
    )
    parser.add_argument("--list", action="store_true", help="list check IDs")
    parser.add_argument("--only", help="comma-separated check IDs")
    parser.add_argument("--base-ref", help="Git revision used for PR diff checks")
    parser.add_argument(
        "--internal-yaml-check",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    env = os.environ if environ is None else environ
    if args.internal_yaml_check:
        try:
            return _internal_yaml_check(_ROOT)
        except Exception as exc:
            print(f"STAGE10 YAML FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    raw_base_ref = args.base_ref
    if raw_base_ref is None:
        raw_base_ref = env.get("STAGE10_BASE_REF", "").strip() or None
    try:
        base_ref = (
            resolve_base_ref(_ROOT, raw_base_ref)
            if raw_base_ref is not None
            else None
        )
        registry = build_registry(_ROOT, sys.executable, base_ref)
        if args.list:
            for spec in registry:
                print(f"{spec.id}\t{spec.name}")
            return 0
        selected = select_checks(registry, args.only)
    except ValueError as exc:
        print(f"stage10_ci_gate.py: error: {exc}", file=sys.stderr)
        return 2
    results = run_gate(selected, _ROOT)
    print(render_console(results), end="")
    summary_path = env.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            append_github_summary(summary_path, render_markdown(results))
        except ValueError as exc:
            print(f"Stage 10 summary failed: {exc}", file=sys.stderr)
            return 1
    return 0 if all(item.status == "passed" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 12: Run Task 2 tests and checkpoint**

Run:

```powershell
uv run --with pyyaml python -m unittest -v tests.test_stage10_ci_gate
uv run --with pyyaml python scripts/stage10_ci_gate.py --list
uv run --with pyyaml python scripts/stage10_ci_gate.py --only stage9-report
```

Expected: all Stage 10 unit tests pass; the list has six IDs; the selected real
check ends with `STAGE10 CI GATE PASSED: 1/1 checks`. The default full gate is
not required yet because the workflow YAML does not exist. Do not stage or
commit; send Task 2 to a fresh reviewer.

---

### Task 3: GitHub Actions adapter and read-only integration contracts

**Files:**
- Create: `.github/workflows/benchmark-regression.yml`
- Modify: `tests/test_stage10_ci_gate.py`

**Interfaces:**
- Consumes: the default Task 2 CLI and `STAGE10_BASE_REF`.
- Produces: a GitHub Actions workflow that invokes the full default registry and
  a real integration proof that selected checks do not mutate Stage 4–9 inputs.

- [ ] **Step 1: Add failing workflow contract test**

Append:

```python
import yaml


class WorkflowContractTests(unittest.TestCase):
    def test_workflow_is_read_only_pinned_and_runs_full_gate(self):
        path = ROOT / ".github" / "workflows" / "benchmark-regression.yml"
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual(
            {"contents": "read"},
            workflow["permissions"],
        )
        self.assertEqual(["main"], workflow["on"]["push"]["branches"])
        self.assertIn("pull_request", workflow["on"])
        self.assertIn("workflow_dispatch", workflow["on"])
        self.assertTrue(workflow["concurrency"]["cancel-in-progress"])
        job = workflow["jobs"]["regression"]
        self.assertEqual("ubuntu-latest", job["runs-on"])
        self.assertEqual(10, job["timeout-minutes"])
        steps = job["steps"]
        self.assertEqual(
            "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd",
            steps[0]["uses"],
        )
        self.assertEqual(0, steps[0]["with"]["fetch-depth"])
        self.assertFalse(steps[0]["with"]["persist-credentials"])
        self.assertEqual(
            "astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b",
            steps[1]["uses"],
        )
        self.assertEqual("0.11.31", steps[1]["with"]["version"])
        self.assertEqual("3.12", steps[1]["with"]["python-version"])
        gate = steps[2]
        self.assertEqual(
            "uv run --python 3.12 --with pyyaml python "
            "scripts/stage10_ci_gate.py",
            gate["run"],
        )
        self.assertIn("pull_request.base.sha", gate["env"]["STAGE10_BASE_REF"])
        forbidden = ("stage4_", "stage7_", "stage8_", "--write", "GEMINI_API_KEY")
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            self.assertNotIn(token, text)
```

- [ ] **Step 2: Run the workflow test and confirm RED**

Run:

```powershell
uv run --with pyyaml python -m unittest -v \
  tests.test_stage10_ci_gate.WorkflowContractTests
```

Expected: FAIL because the workflow file is missing.

- [ ] **Step 3: Create the thin pinned workflow**

Create `.github/workflows/benchmark-regression.yml`:

```yaml
name: Benchmark regression

'on':
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: stage10-${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

jobs:
  regression:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - name: Checkout
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
        with:
          fetch-depth: 0
          persist-credentials: false

      - name: Install uv and Python
        uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b # v8.1.0
        with:
          version: "0.11.31"
          python-version: "3.12"
          enable-cache: false

      - name: Run strict offline gate
        env:
          STAGE10_BASE_REF: ${{ github.event.pull_request.base.sha || '' }}
        run: uv run --python 3.12 --with pyyaml python scripts/stage10_ci_gate.py
```

- [ ] **Step 4: Run the workflow contract and YAML checks**

Run:

```powershell
uv run --with pyyaml python -m unittest -v \
  tests.test_stage10_ci_gate.WorkflowContractTests
uv run --with pyyaml python scripts/stage10_ci_gate.py --only yaml
```

Expected: workflow test passes and the selected YAML gate ends with
`STAGE10 CI GATE PASSED: 1/1 checks`.

- [ ] **Step 5: Add failing stale-output and no-mutation integration tests**

Append:

```python
import hashlib
import json


def snapshot_files(paths):
    snapshot = {}
    for base in paths:
        if base.is_file():
            files = (base,)
        elif base.exists():
            files = tuple(path for path in base.rglob("*") if path.is_file())
        else:
            files = ()
        for path in files:
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            data = path.read_bytes()
            snapshot[path.relative_to(ROOT).as_posix()] = (
                len(data),
                path.stat().st_mtime_ns,
                hashlib.sha256(data).hexdigest(),
            )
    return snapshot


class ReadOnlyIntegrationTests(unittest.TestCase):
    def test_selected_real_checks_do_not_mutate_stage4_through_stage9(self):
        protected = (
            ROOT / "config",
            ROOT / "results",
            ROOT / "docs" / "stage9-bao-cao-cuoi.md",
        )
        before = snapshot_files(protected)
        code = GATE.main(
            ["--only", "stage9-report,yaml"],
            {},
        )
        after = snapshot_files(protected)
        self.assertEqual(0, code)
        self.assertEqual(before, after)

    def test_stale_stage9_failure_is_reported_without_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            scripts = root / "scripts"
            scripts.mkdir()
            marker = root / "stage9.txt"
            marker.write_text("stale\n", encoding="utf-8", newline="\n")
            checker = scripts / "stage9_report.py"
            checker.write_text(
                "import pathlib,sys\n"
                "text=pathlib.Path('stage9.txt').read_text(encoding='utf-8')\n"
                "raise SystemExit(0 if text == 'fresh\\n' else 1)\n",
                encoding="utf-8",
                newline="\n",
            )
            spec = GATE.CheckSpec(
                "stage9-report",
                "Stage 9 report",
                (sys.executable, "scripts/stage9_report.py", "--check"),
                10,
            )
            result = GATE.run_check(spec, root)
            self.assertEqual("failed", result.status)
            self.assertEqual("stale\n", marker.read_text(encoding="utf-8"))
```

- [ ] **Step 6: Run the new integration acceptance tests**

Run:

```powershell
uv run --with pyyaml python -m unittest -v \
  tests.test_stage10_ci_gate.ReadOnlyIntegrationTests
```

Expected: both tests pass. If the real selected checks change any protected
file, keep the failing snapshot assertion and fix the production behavior; do
not weaken the protected path set or remove modification-time comparison.

- [ ] **Step 7: Run integration tests and the full gate**

Run:

```powershell
uv run --with pyyaml python -m unittest -v \
  tests.test_stage10_ci_gate.ReadOnlyIntegrationTests
uv run --with pyyaml python scripts/stage10_ci_gate.py
```

Expected: integration tests pass; the default command runs six checks and ends
with `STAGE10 CI GATE PASSED: 6/6 checks`.

- [ ] **Step 8: Task 3 checkpoint**

Run:

```powershell
uv run --with pyyaml python -m unittest discover -s tests -v
uv run --with pyyaml python scripts/stage9_report.py --check
git diff --check
```

Expected: all tests pass, Stage 9 prints `STAGE9 REPORT OK`, and `git diff
--check` exits `0` (existing Windows LF-to-CRLF warnings are not whitespace
errors). Do not stage or commit; send Task 3 to a fresh reviewer.

---

### Task 4: Operator documentation, roadmap completion, and final verification

**Files:**
- Create: `docs/stage10-ci-regression.md`
- Modify: `README.md`
- Modify: `docs/00-tong-quan.md`
- Modify: `tests/test_stage10_ci_gate.py`

**Interfaces:**
- Consumes: the finished CLI and GitHub workflow.
- Produces: discoverable local/CI instructions and a completed ten-stage
  roadmap without changing Stage 9 metrics or recommendations.

- [ ] **Step 1: Add failing documentation contract**

Append:

```python
class DocumentationContractTests(unittest.TestCase):
    def test_stage10_is_complete_and_linked(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        overview = (ROOT / "docs" / "00-tong-quan.md").read_text(
            encoding="utf-8"
        )
        guide = ROOT / "docs" / "stage10-ci-regression.md"
        self.assertIn("## Lộ trình 10 giai đoạn", readme)
        self.assertIn(
            "| 10 | CI regression gate | ✅ Hoàn tất | "
            "[docs/stage10-ci-regression.md](docs/stage10-ci-regression.md) |",
            readme,
        )
        self.assertIn(
            "[stage10](stage10-ci-regression.md)",
            overview,
        )
        text = guide.read_text(encoding="utf-8")
        self.assertIn("scripts/stage10_ci_gate.py", text)
        self.assertIn("không gọi scanner, API hoặc judge", text)
        self.assertIn("GITHUB_STEP_SUMMARY", text)
```

- [ ] **Step 2: Run the documentation test and confirm RED**

Run:

```powershell
uv run --with pyyaml python -m unittest -v \
  tests.test_stage10_ci_gate.DocumentationContractTests
```

Expected: FAIL because the guide, ten-stage heading, row, and overview link are
missing.

- [ ] **Step 3: Write the operator guide**

Create `docs/stage10-ci-regression.md` with these concrete sections and
commands:

````markdown
# Giai đoạn 10 — CI regression gate

> ✅ **KẾT LUẬN:** Mỗi pull request chạy một strict gate offline; bất kỳ test,
> artifact, provenance, metric, Pareto, budget, cú pháp hoặc report freshness
> nào sai đều block PR.

## 1. Chạy local

```bash
# Chạy đủ sáu check
uv run --with pyyaml python scripts/stage10_ci_gate.py

# Liệt kê check hoặc chạy một nhóm để chẩn đoán
uv run --with pyyaml python scripts/stage10_ci_gate.py --list
uv run --with pyyaml python scripts/stage10_ci_gate.py \
  --only stage9-report,yaml

# Kiểm whitespace trên khoảng thay đổi so với một commit gốc
uv run --with pyyaml python scripts/stage10_ci_gate.py \
  --base-ref <git-revision>
```

## 2. Sáu check bắt buộc

| Check | Kiểm tra | Khi thất bại |
|---|---|---|
| `unit-tests` | Toàn bộ `unittest` trong `tests/` | Có regression hành vi hoặc contract |
| `python-compile` | Compile `scripts/` và `proxy/token_logger.py` | Python có lỗi cú pháp/import-time |
| `yaml` | Parse benchmark, proxy và workflow YAML | Cấu hình hoặc workflow không còn hợp lệ |
| `bash-syntax` | `bash -n` cho `scripts/*.sh`, `adapters/*.sh` | Script shell có lỗi cú pháp |
| `stage9-report` | `stage9_report.py --check` | Artifact, provenance, metric, Pareto, budget hoặc report bị stale/mâu thuẫn |
| `whitespace` | `git diff --check` trên working tree hoặc PR range | Diff có whitespace error |

## 3. Chính sách lỗi

- Gate luôn chạy hết các check đã chọn để trả về đầy đủ lỗi trong một lần.
- Exit `0` chỉ khi tất cả check đạt; exit `1` khi có check lỗi; exit `2` khi
  tham số CLI không hợp lệ.
- Timeout, executable bị thiếu và exception đều là lỗi blocking.
- Gate không tự sửa report stale. Việc tái sinh bằng `--write` phải là quyết
  định có chủ đích sau khi xác nhận source artifact thay đổi hợp lệ.
- Trong GitHub Actions, cùng kết quả được nối vào `GITHUB_STEP_SUMMARY`.

## 4. Offline và read-only

Gate không gọi scanner, API hoặc judge; không khởi động proxy; không chạy
`--write`; không sửa artifact Stage 4–9. Workflow không nhận secret và chỉ có
quyền `contents: read`.

## 5. GitHub Actions

Workflow `.github/workflows/benchmark-regression.yml` chạy khi:

- mở hoặc cập nhật pull request;
- push vào `main`;
- chạy thủ công bằng `workflow_dispatch`.

Job dùng Ubuntu, Python 3.12, timeout 10 phút và concurrency
`cancel-in-progress`. `actions/checkout` và `astral-sh/setup-uv` được pin bằng
commit SHA; uv được pin phiên bản. Pull request truyền base SHA qua
`STAGE10_BASE_REF` để check đúng toàn bộ PR diff.

## 6. Chẩn đoán

- Report stale:

  ```bash
  uv run --with pyyaml python scripts/stage9_report.py --check
  ```

  Chỉ chạy `stage9_report.py --write` khi thay đổi source artifact là có chủ
  đích và đã được review.

- Lỗi một nhóm:

  ```bash
  uv run --with pyyaml python scripts/stage10_ci_gate.py --only <check-id>
  ```

- Whitespace:

  ```bash
  git diff --check HEAD --
  ```

- Base revision:

  ```bash
  git rev-parse --verify --end-of-options <revision>^{commit}
  ```

Không bỏ check hoặc đổi strict gate thành warning để làm xanh CI.
````

- [ ] **Step 4: Update both roadmaps**

In `README.md`:

- change `## Lộ trình 9 giai đoạn` to `## Lộ trình 10 giai đoạn`;
- add this exact row after Stage 9:

```markdown
| 10 | CI regression gate | ✅ Hoàn tất | [docs/stage10-ci-regression.md](docs/stage10-ci-regression.md) |
```

- add the default Stage 10 command after the Stage 9 report commands in the
  quick-start block:

```bash
# GĐ10: strict regression gate offline dùng local và trên pull request
uv run --with pyyaml python scripts/stage10_ci_gate.py
```

In `docs/00-tong-quan.md`, add after Stage 9:

```markdown
10. **CI regression gate** ([stage10](stage10-ci-regression.md)) — chạy toàn bộ
    contract offline trên mỗi pull request và block khi test, artifact,
    provenance, metric, Pareto, budget, cú pháp hoặc report freshness bị lệch.
```

- [ ] **Step 5: Run documentation tests and confirm GREEN**

Run the Step 2 command.

Expected: the documentation contract passes.

- [ ] **Step 6: Fresh full verification**

Run every command separately and retain its exit code/output:

```powershell
uv run --with pyyaml python -m unittest discover -s tests -v
uv run --with pyyaml python scripts/stage10_ci_gate.py
uv run --with pyyaml python scripts/stage9_report.py --check
uv run --with pyyaml python -m compileall -q scripts proxy/token_logger.py
uv run --with pyyaml python -c "import pathlib,yaml; [yaml.safe_load(path.read_text(encoding='utf-8')) for path in (pathlib.Path('config/benchmark.yaml'), pathlib.Path('proxy/litellm_config.yaml'), pathlib.Path('.github/workflows/benchmark-regression.yml'))]"
& 'C:\Program Files\Git\bin\bash.exe' -n scripts/*.sh adapters/*.sh
git diff --check
git status --short
```

Expected:

- full suite: `OK`;
- Stage 10: `STAGE10 CI GATE PASSED: 6/6 checks`;
- Stage 9: `STAGE9 REPORT OK`;
- compile, YAML, and Bash syntax: exit `0`;
- `git diff --check`: exit `0`, with at most pre-existing LF-to-CRLF warnings;
- status contains only intended Stage 10 files plus preserved earlier work.

- [ ] **Step 7: Verify strict failure without persistent mutation**

Use a temporary copy of the Stage 9 Markdown output, not the real artifact:

```powershell
uv run --with pyyaml python -m unittest -v `
  tests.test_stage10_ci_gate.ReadOnlyIntegrationTests.test_stale_stage9_failure_is_reported_without_repair
```

Expected: test passes, proving a stale condition becomes a failed result and is
not repaired.

- [ ] **Step 8: Final review checkpoint**

Provide a fresh reviewer:

- the approved design;
- this plan;
- `scripts/stage10_ci_gate.py`;
- `tests/test_stage10_ci_gate.py`;
- `.github/workflows/benchmark-regression.yml`;
- the Stage 10 guide and roadmap diffs;
- the full verification output.

The reviewer must re-probe read-only behavior, no-shell argv execution, base-ref
handling, all-failure aggregation, exact workflow permissions/pins, and
non-recursive tests. Resolve every Critical or Important issue, rerun full
verification, and request a final whole-change review. Do not stage or commit.
