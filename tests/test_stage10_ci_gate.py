import hashlib
import importlib.util
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import yaml
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "stage10_ci_gate.py"
SPEC = importlib.util.spec_from_file_location("stage10_ci_gate", MODULE_PATH)
GATE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = GATE
SPEC.loader.exec_module(GATE)

STAGE9_MODULE_PATH = ROOT / "scripts" / "stage9_report.py"
STAGE9_SPEC = importlib.util.spec_from_file_location(
    "stage10_stage9_report", STAGE9_MODULE_PATH
)
STAGE9 = importlib.util.module_from_spec(STAGE9_SPEC)
assert STAGE9_SPEC.loader is not None
STAGE9_SPEC.loader.exec_module(STAGE9)


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

    def test_missing_bash_executable_has_installation_guidance(self):
        spec = GATE.CheckSpec("bash-syntax", "Bash syntax", ("bash", "-n"), 5)

        def fake_run(*args, **kwargs):
            raise FileNotFoundError("bash missing")

        result = GATE.run_check(
            spec,
            ROOT,
            run=fake_run,
            clock=self.clock(),
        )
        self.assertEqual("failed", result.status)
        self.assertEqual(
            "FileNotFoundError: Bash is unavailable. "
            "Install Bash and ensure 'bash' is in PATH.",
            result.message,
        )

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

    def test_invalid_utf8_diagnostic_is_replaced_and_tail_bounded(self):
        marker = b"\xffTAIL"

        def fake_run(*args, **kwargs):
            return types.SimpleNamespace(
                returncode=9,
                stdout=b"x" * (GATE._MAX_DIAGNOSTIC_CHARS + 100) + marker,
                stderr=b"",
            )

        result = GATE.run_check(
            self.spec,
            ROOT,
            run=fake_run,
            clock=self.clock(),
        )
        self.assertEqual("failed", result.status)
        self.assertEqual(9, result.return_code)
        self.assertLessEqual(len(result.message), GATE._MAX_DIAGNOSTIC_CHARS)
        self.assertTrue(result.message.endswith("\ufffdTAIL"))


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

    def test_base_ref_runner_failures_are_infrastructure_errors_with_causes(self):
        errors = (
            FileNotFoundError("git missing"),
            subprocess.TimeoutExpired(("git",), 30, output="partial"),
            RuntimeError("runner boom"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(GATE.subprocess, "run", side_effect=error):
                    with self.assertRaises(GATE.InfrastructureError) as raised:
                        GATE.resolve_base_ref(ROOT, "HEAD")
                self.assertIs(error, raised.exception.__cause__)
                self.assertIn(type(error).__name__, str(raised.exception))
                self.assertNotIn("Traceback", str(raised.exception))

    def test_missing_shell_files_becomes_bash_preflight_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            registry = GATE.build_registry(root, "python", None)
            bash = next(item for item in registry if item.id == "bash-syntax")
            self.assertIn("no Bash scripts found", bash.preflight_error)
            result = GATE.run_check(
                bash,
                root,
                run=lambda *args, **kwargs: self.fail("must not execute Bash"),
            )
        self.assertEqual("failed", result.status)
        self.assertIn("no Bash scripts found", result.message)


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
        self.assertIn("return code 3", console)
        self.assertIn("return code 3", markdown)
        self.assertIn("broken", markdown)


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

    def test_main_exit_matrix_runs_every_selected_check(self):
        specs = (
            GATE.CheckSpec("alpha", "Alpha", ("alpha",), 1),
            GATE.CheckSpec("beta", "Beta", ("beta",), 1),
        )
        cases = (
            (frozenset(), 0),
            (frozenset({"beta"}), 1),
            (frozenset({"alpha", "beta"}), 1),
        )
        for failed_ids, expected_code in cases:
            with self.subTest(failed_ids=sorted(failed_ids)):
                seen = []

                def fake_gate(selected, root):
                    results = []
                    for spec in selected:
                        seen.append(spec.id)
                        failed = spec.id in failed_ids
                        results.append(
                            GATE.CheckResult(
                                spec.id,
                                spec.name,
                                "failed" if failed else "passed",
                                0,
                                "broken" if failed else "ok",
                                7 if failed else 0,
                            )
                        )
                    return tuple(results)

                with (
                    mock.patch.object(
                        GATE, "build_registry", return_value=specs
                    ),
                    mock.patch.object(GATE, "run_gate", side_effect=fake_gate),
                    redirect_stdout(io.StringIO()),
                ):
                    code = GATE.main([], {})
                self.assertEqual(expected_code, code)
                self.assertEqual(["alpha", "beta"], seen)

    def test_main_rejects_all_only_usage_errors_with_exit_two(self):
        registry = (GATE.CheckSpec("alpha", "Alpha", ("alpha",), 1),)
        for raw in ("", "alpha,alpha", "missing"):
            with self.subTest(raw=raw):
                stderr = io.StringIO()
                with (
                    mock.patch.object(
                        GATE, "build_registry", return_value=registry
                    ),
                    mock.patch.object(GATE, "run_gate") as run_gate,
                    redirect_stderr(stderr),
                ):
                    code = GATE.main(["--only", raw], {})
                self.assertEqual(2, code)
                self.assertIn("stage10_ci_gate.py: error:", stderr.getvalue())
                run_gate.assert_not_called()

    def test_base_ref_infrastructure_failure_aggregates_and_exits_one(self):
        errors = (
            FileNotFoundError("git missing"),
            subprocess.TimeoutExpired(("git",), 30, output="partial"),
            RuntimeError("runner boom"),
        )
        expected_ids = [
            "unit-tests",
            "python-compile",
            "yaml",
            "bash-syntax",
            "stage9-report",
            "whitespace",
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                seen = []

                def fake_gate(specs, root):
                    results = []
                    for spec in specs:
                        seen.append(spec.id)
                        if spec.preflight_error:
                            results.append(GATE.run_check(spec, root))
                        else:
                            results.append(
                                GATE.CheckResult(
                                    spec.id, spec.name, "passed", 0, "ok", 0
                                )
                            )
                    return tuple(results)

                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch.object(GATE.subprocess, "run", side_effect=error),
                    mock.patch.object(GATE, "run_gate", side_effect=fake_gate),
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                ):
                    code = GATE.main(["--base-ref", "HEAD"], {})
                self.assertEqual(1, code)
                self.assertEqual(expected_ids, seen)
                self.assertIn("[whitespace]", stdout.getvalue())
                self.assertIn(type(error).__name__, stdout.getvalue())
                self.assertNotIn("Traceback", stdout.getvalue() + stderr.getvalue())

    def test_base_ref_is_not_resolved_without_whitespace_selection(self):
        seen = []

        def fake_gate(specs, root):
            seen.extend(item.id for item in specs)
            return tuple(
                GATE.CheckResult(item.id, item.name, "passed", 0, "ok", 0)
                for item in specs
            )

        with (
            mock.patch.object(GATE, "resolve_base_ref") as resolve,
            mock.patch.object(GATE, "run_gate", side_effect=fake_gate),
            redirect_stdout(io.StringIO()),
        ):
            code = GATE.main(
                ["--only", "stage9-report", "--base-ref", "HEAD"],
                {},
            )
        self.assertEqual(0, code)
        self.assertEqual(["stage9-report"], seen)
        resolve.assert_not_called()

    def test_invalid_base_revision_is_still_usage_error(self):
        stderr = io.StringIO()
        with (
            mock.patch.object(
                GATE,
                "resolve_base_ref",
                side_effect=ValueError("invalid base revision 'missing'"),
            ),
            mock.patch.object(GATE, "run_gate") as run_gate,
            redirect_stderr(stderr),
        ):
            code = GATE.main(["--base-ref", "missing"], {})
        self.assertEqual(2, code)
        self.assertIn("invalid base revision", stderr.getvalue())
        run_gate.assert_not_called()

    def test_missing_shell_files_aggregates_as_bash_failure_and_exits_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            seen = []

            def fake_gate(specs, current_root):
                results = []
                for spec in specs:
                    seen.append(spec.id)
                    if spec.preflight_error:
                        results.append(GATE.run_check(spec, current_root))
                    else:
                        results.append(
                            GATE.CheckResult(
                                spec.id, spec.name, "passed", 0, "ok", 0
                            )
                        )
                return tuple(results)

            stdout = io.StringIO()
            with (
                mock.patch.object(GATE, "_ROOT", root),
                mock.patch.object(GATE, "run_gate", side_effect=fake_gate),
                redirect_stdout(stdout),
            ):
                code = GATE.main([], {})
        self.assertEqual(1, code)
        self.assertEqual(6, len(seen))
        self.assertIn("[bash-syntax]", stdout.getvalue())
        self.assertIn("no Bash scripts found", stdout.getvalue())

    def test_missing_bash_executable_is_a_check_failure_with_guidance(self):
        seen = []

        def fake_gate(specs, root):
            results = []
            for spec in specs:
                seen.append(spec.id)
                if spec.id == "bash-syntax":
                    def missing_bash(*args, **kwargs):
                        raise FileNotFoundError("bash missing")

                    results.append(
                        GATE.run_check(spec, root, run=missing_bash)
                    )
                else:
                    results.append(
                        GATE.CheckResult(
                            spec.id, spec.name, "passed", 0, "ok", 0
                        )
                    )
            return tuple(results)

        stdout = io.StringIO()
        with (
            mock.patch.object(GATE, "run_gate", side_effect=fake_gate),
            redirect_stdout(stdout),
        ):
            code = GATE.main([], {})
        self.assertEqual(1, code)
        self.assertEqual(6, len(seen))
        self.assertIn("[bash-syntax]", stdout.getvalue())
        self.assertIn(
            "Install Bash and ensure 'bash' is in PATH.",
            stdout.getvalue(),
        )

    def test_environment_base_ref_is_used_when_cli_value_is_absent(self):
        whitespace = GATE.CheckSpec(
            "whitespace",
            "Git whitespace",
            ("git", "diff", "--check", "HEAD", "--"),
            30,
        )
        with (
            mock.patch.object(
                GATE,
                "resolve_base_ref",
                return_value="a" * 40,
            ) as resolve,
            mock.patch.object(
                GATE, "build_registry", return_value=(whitespace,)
            ) as build,
            mock.patch.object(
                GATE,
                "run_gate",
                return_value=(
                    GATE.CheckResult(
                        "whitespace",
                        "Git whitespace",
                        "passed",
                        0,
                        "ok",
                        0,
                    ),
                ),
            ) as run_gate,
        ):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    0, GATE.main([], {"STAGE10_BASE_REF": "base-sha"})
                )
        resolve.assert_called_once_with(GATE._ROOT, "base-sha")
        build.assert_called_once_with(GATE._ROOT, sys.executable, None)
        self.assertEqual(
            ("git", "diff", "--check", f"{'a' * 40}...HEAD", "--"),
            run_gate.call_args.args[0][0].argv,
        )

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
        registry = GATE.build_registry(ROOT, sys.executable, None)
        total_check_seconds = sum(item.timeout_seconds for item in registry)
        self.assertLessEqual(
            total_check_seconds,
            job["timeout-minutes"] * 60 - 120,
        )
        steps = job["steps"]
        self.assertEqual(
            3,
            len(steps),
        )
        self.assertEqual(
            {
                "name": "Checkout",
                "uses": "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd",
                "with": {"fetch-depth": 0, "persist-credentials": False},
            },
            steps[0],
        )
        self.assertEqual(
            {
                "name": "Install uv and Python",
                "uses": "astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b",
                "with": {
                    "version": "0.11.31",
                    "python-version": "3.12",
                    "enable-cache": False,
                },
            },
            steps[1],
        )
        self.assertEqual(
            {
                "name": "Run strict offline gate",
                "env": {
                    "STAGE10_BASE_REF": (
                        "${{ github.event.pull_request.base.sha || '' }}"
                    ),
                },
                "run": (
                    "uv run --python 3.12 --with pyyaml python "
                    "scripts/stage10_ci_gate.py"
                ),
            },
            steps[2],
        )
        gate = steps[2]
        self.assertIn("pull_request.base.sha", gate["env"]["STAGE10_BASE_REF"])
        forbidden = ("stage4_", "stage7_", "stage8_", "--write", "GEMINI_API_KEY")
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            self.assertNotIn(token, text)


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
        stage9_write = (
            "uv run --with pyyaml python scripts/stage9_report.py --write"
        )
        stage9_check = (
            "uv run --with pyyaml python scripts/stage9_report.py --check"
        )
        stage10_default = (
            "uv run --with pyyaml python scripts/stage10_ci_gate.py"
        )
        for command in (stage9_write, stage9_check, stage10_default):
            with self.subTest(readme_command=command):
                self.assertIn(command, readme)
        if all(
            command in readme
            for command in (stage9_write, stage9_check, stage10_default)
        ):
            self.assertLess(
                readme.index(stage9_write), readme.index(stage9_check)
            )
            self.assertLess(
                readme.index(stage9_check), readme.index(stage10_default)
            )

        overview_contract = " ".join(overview.split())
        self.assertIn(
            "contract offline trên mỗi pull request và block khi test, "
            "artifact, provenance, metric, Pareto, budget, cú pháp hoặc "
            "report freshness bị lệch.",
            overview_contract,
        )

        normalized_guide = " ".join(text.replace("\\\n", "").split())
        commands = (
            stage10_default,
            f"{stage10_default} --list",
            f"{stage10_default} --only stage9-report,yaml",
            f"{stage10_default} --base-ref <git-revision>",
        )
        table_rows = (
            "| `unit-tests` | Toàn bộ `unittest` trong `tests/` | "
            "Có regression hành vi hoặc contract |",
            "| `python-compile` | Compile `scripts/` và "
            "`proxy/token_logger.py` | Python có lỗi cú pháp/bytecode |",
            "| `yaml` | Parse benchmark, proxy và workflow YAML | "
            "Cấu hình hoặc workflow không còn hợp lệ |",
            "| `bash-syntax` | `bash -n` cho `scripts/*.sh`, "
            "`adapters/*.sh` | Script shell có lỗi cú pháp |",
            "| `stage9-report` | `stage9_report.py --check` | Artifact, "
            "provenance, metric, Pareto, budget hoặc report bị stale/mâu thuẫn |",
            "| `whitespace` | `git diff --check` trên working tree hoặc "
            "PR range | Diff có whitespace error |",
        )
        guide_contract = (
            *commands,
            *table_rows,
            "Exit `0` chỉ khi tất cả check đạt; exit `1` khi có check lỗi; "
            "exit `2` khi",
            "không gọi scanner, API hoặc judge; không khởi động proxy; không chạy",
            "`--write`; không sửa artifact Stage 4–9.",
            "GITHUB_STEP_SUMMARY",
            "mở hoặc cập nhật pull request;",
            "push vào `main`;",
            "chạy thủ công bằng `workflow_dispatch`.",
            "quyền `contents: read`.",
            "Python 3.12, timeout 10 phút và concurrency",
            "`cancel-in-progress`.",
            "Job dùng Ubuntu, Python 3.12, timeout 10 phút",
            "`actions/checkout` và `astral-sh/setup-uv` được pin bằng",
            "commit SHA; uv được pin phiên bản.",
            "base SHA qua",
            "`STAGE10_BASE_REF` để check đúng toàn bộ PR diff.",
            "Workflow không nhận secret",
            "Cài Bash và đảm bảo `bash` có trong `PATH`.",
        )
        for expected in guide_contract:
            with self.subTest(guide_contract=expected):
                self.assertIn(expected, normalized_guide)


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
            config = yaml.safe_load(
                (ROOT / "config" / "benchmark.yaml").read_text(encoding="utf-8")
            )
            sources = STAGE9.source_paths(
                ROOT,
                config["stage8"]["active_profile"],
            )
            files = (
                ROOT / "scripts" / "stage9_report.py",
                ROOT / "scripts" / "stage7c_judge_agreement.py",
                *sources.values(),
                *(ROOT / relative for relative in (STAGE9._SUMMARY_REL, STAGE9._REPORT_REL)),
            )
            for source in files:
                target = root / source.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)

            report = root / STAGE9._REPORT_REL
            report.write_bytes(b"stale Stage 9 output\n")
            stale = report.read_bytes()
            spec = next(
                item
                for item in GATE.build_registry(ROOT, sys.executable, None)
                if item.id == "stage9-report"
            )
            self.assertEqual(
                (sys.executable, "scripts/stage9_report.py", "--check"),
                spec.argv,
            )
            result = GATE.run_check(spec, root)
            self.assertEqual("failed", result.status)
            self.assertEqual(stale, report.read_bytes())
