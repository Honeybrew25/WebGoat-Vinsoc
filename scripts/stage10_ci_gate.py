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


class InfrastructureError(RuntimeError):
    """An execution-environment failure distinct from invalid user input."""


@dataclasses.dataclass(frozen=True)
class CheckSpec:
    id: str
    name: str
    argv: tuple[str, ...]
    timeout_seconds: float
    preflight_error: str | None = None

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
    if spec.preflight_error is not None:
        return CheckResult(
            id=spec.id,
            name=spec.name,
            status="failed",
            duration_seconds=0.0,
            message=spec.preflight_error,
            return_code=None,
        )
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
        if spec.id == "bash-syntax":
            message = (
                "FileNotFoundError: Bash is unavailable. "
                "Install Bash and ensure 'bash' is in PATH."
            )
        else:
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


def _relative_strings(
    root: pathlib.Path, paths: Sequence[pathlib.Path]
) -> tuple[str, ...]:
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
    bash_preflight_error = None
    if not shell_files:
        bash_preflight_error = (
            "no Bash scripts found under scripts/ or adapters/"
        )
    script = pathlib.Path(__file__).resolve()
    whitespace = (git, "diff", "--check", "HEAD", "--")
    if base_ref:
        whitespace = (git, "diff", "--check", f"{base_ref}...HEAD", "--")
    return (
        CheckSpec(
            "unit-tests",
            "Unit tests",
            (python, "-m", "unittest", "discover", "-s", "tests", "-v"),
            240.0,
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
            45.0,
        ),
        CheckSpec(
            "yaml",
            "YAML parse",
            (python, str(script), "--internal-yaml-check"),
            30.0,
        ),
        CheckSpec(
            "bash-syntax",
            "Bash syntax",
            (bash, "-n", *shell_files),
            30.0,
            preflight_error=bash_preflight_error,
        ),
        CheckSpec(
            "stage9-report",
            "Stage 9 report",
            (python, "scripts/stage9_report.py", "--check"),
            45.0,
        ),
        CheckSpec("whitespace", "Git whitespace", whitespace, 30.0),
    )


def resolve_base_ref(
    root: pathlib.Path, raw: str, *, git: str = "git"
) -> str:
    if not raw or raw.startswith("-"):
        raise ValueError(f"invalid base revision: {raw!r}")
    try:
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
    except Exception as exc:
        detail = _bounded_message("", str(exc))
        raise InfrastructureError(
            f"base revision resolution failed: {type(exc).__name__}: {detail}"
        ) from exc
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


def run_gate(
    specs: tuple[CheckSpec, ...],
    root: pathlib.Path,
    *,
    execute: Callable[[CheckSpec, pathlib.Path], CheckResult] = run_check,
) -> tuple[CheckResult, ...]:
    return tuple(execute(spec, root) for spec in specs)


def _label(status: str) -> str:
    return "PASS" if status == "passed" else "FAIL"


def _failure_message(result: CheckResult) -> str:
    if result.return_code is None:
        return result.message
    return f"return code {result.return_code}\n{result.message}"


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
            lines.extend((f"[{item.id}]", _failure_message(item)))
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
            safe = _failure_message(item).replace("```", "'''")
            lines.extend((f"#### `{item.id}`", "", "```text", safe, "```", ""))
    else:
        lines.extend(("", f"**PASSED: {len(results)}/{len(results)} checks.**", ""))
    return "\n".join(lines).rstrip() + "\n"


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
            print(
                f"STAGE10 YAML FAILED: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 1
    raw_base_ref = args.base_ref
    if raw_base_ref is None:
        raw_base_ref = env.get("STAGE10_BASE_REF", "").strip() or None
    try:
        registry = build_registry(_ROOT, sys.executable, None)
        if args.list:
            for spec in registry:
                print(f"{spec.id}\t{spec.name}")
            return 0
        selected = select_checks(registry, args.only)
    except ValueError as exc:
        print(f"stage10_ci_gate.py: error: {exc}", file=sys.stderr)
        return 2
    if raw_base_ref is not None and any(
        spec.id == "whitespace" for spec in selected
    ):
        try:
            base_ref = resolve_base_ref(_ROOT, raw_base_ref)
        except ValueError as exc:
            print(f"stage10_ci_gate.py: error: {exc}", file=sys.stderr)
            return 2
        except InfrastructureError as exc:
            selected = tuple(
                dataclasses.replace(spec, preflight_error=str(exc))
                if spec.id == "whitespace"
                else spec
                for spec in selected
            )
        else:
            selected = tuple(
                dataclasses.replace(
                    spec,
                    argv=(
                        spec.argv[0],
                        "diff",
                        "--check",
                        f"{base_ref}...HEAD",
                        "--",
                    ),
                )
                if spec.id == "whitespace"
                else spec
                for spec in selected
            )
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
