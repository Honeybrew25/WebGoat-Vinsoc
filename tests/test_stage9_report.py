import importlib.util
import json
import tempfile
import unittest
import copy
import math
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

    def test_approximate_f1_rejects_nonfinite_input(self):
        with self.assertRaisesRegex(ValueError, "precision"):
            REPORT.approximate_f1(float("inf"), 1, 2)

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

    def test_resource_summary_rejects_nonfinite_measurement(self):
        row = {
            "tool": "arm-metis",
            "wall_clock_s": 1,
            "input_tokens": 1,
            "output_tokens": 1,
            "cost_usd": 1,
        }
        optimized = dict(row, total_tokens=2, cost_usd=float("inf"))
        with self.assertRaisesRegex(ValueError, "cost_usd"):
            REPORT.resource_summary([row], [optimized], ["arm-metis"])

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

    def test_canonical_json_rejects_nonfinite_numbers(self):
        with self.assertRaisesRegex(ValueError, "Out of range float"):
            REPORT.canonical_json({"not_a_number": float("nan")})
        with self.assertRaisesRegex(ValueError, "non-finite"):
            REPORT._assert_close("comparison", math.inf, 1.0)

    def test_money_sum_has_cross_runtime_canonical_precision(self):
        self.assertEqual(
            1.871116,
            REPORT._money_sum([0.448891, 0.445274, 0.445274, 0.184089, 0.176114, 0.171474]),
        )

    def test_stage7b_recall_artifact_is_byte_exact(self):
        self.assertEqual(
            "cfba485cf9797438f6bd50f2473f511212018f2bbc1b608c7ce03814dabd712b",
            REPORT.sha256_file(ROOT / "results" / "stats" / "recall.json"),
        )


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
        self.assertEqual(1.871116, summary["budget"]["run_spend_usd"])
        self.assertEqual(1.872177, summary["budget"]["actual_spend_usd"])
        self.assertEqual(
            {"arm-metis", "datadog-saist"},
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

    def test_builder_rejects_unlisted_optimized_cost_row(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        extra = dict(broken["optimized_cost"][0])
        extra["tool"] = "unlisted-tool"
        broken["optimized_cost"].append(extra)
        with self.assertRaisesRegex(ValueError, "optimized tools"):
            REPORT.build_summary_from_sources(broken)

    def test_builder_rejects_non_independent_final_verdict(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["judged_final"][0]["verdict_source"] = "baseline-same-model"
        with self.assertRaisesRegex(ValueError, "invalid final verdict provenance"):
            REPORT.build_summary_from_sources(broken)

    def test_builder_rejects_duplicate_alignment_key_without_count_change(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["judged_same"][-1] = dict(broken["judged_same"][0])
        with self.assertRaisesRegex(ValueError, "duplicate same-model"):
            REPORT.build_summary_from_sources(broken)

    def test_builder_rejects_non_binary_baseline_verdict(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["judged_independent"][0]["verdict"] = "UNKNOWN"
        with self.assertRaisesRegex(ValueError, "independent verdict"):
            REPORT.build_summary_from_sources(broken)

    def test_builder_rejects_agreement_precision_that_disagrees_with_artifact(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["agreement"]["precision_by_tool"]["arm-metis"]["weak"] = 0.0
        with self.assertRaisesRegex(ValueError, "agreement same-model precision"):
            REPORT.build_summary_from_sources(broken)

    def test_builder_rejects_duplicate_run_identifier(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["optimized_cost"][-1]["run"] = "run-02-warm"
        with self.assertRaisesRegex(ValueError, "optimized run IDs"):
            REPORT.build_summary_from_sources(broken)

    def test_builder_rejects_wrong_run_phase(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["baseline_cost"][0]["phase"] = "warm"
        with self.assertRaisesRegex(ValueError, "baseline phase"):
            REPORT.build_summary_from_sources(broken)

    def test_builder_requires_exactly_three_cold_warm_runs(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["config"]["run"]["repeats"] = 2
        with self.assertRaisesRegex(ValueError, "exactly three"):
            REPORT.build_summary_from_sources(broken)

    def test_builder_rejects_nonfinite_run_measurement(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["optimized_cost"][0]["cost_usd"] = float("nan")
        with self.assertRaisesRegex(ValueError, "optimized .*cost_usd"):
            REPORT.build_summary_from_sources(broken)

    def test_builder_rejects_nonfinite_final_stability_count(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["judged_final"][0]["run_count"] = float("inf")
        with self.assertRaisesRegex(ValueError, "final .*run_count"):
            REPORT.build_summary_from_sources(broken)

    def test_builder_rejects_unconfigured_final_verdict_tool(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["judged_final"][0]["tool"] = "unconfigured-tool"
        with self.assertRaisesRegex(ValueError, "final verdict tools"):
            REPORT.build_summary_from_sources(broken)

    def test_builder_rejects_final_budget_that_differs_from_profile_and_config(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["final"]["budget_usd"] = 4.0
        with self.assertRaisesRegex(ValueError, "final budget"):
            REPORT.build_summary_from_sources(broken)

    def test_builder_rejects_profile_knob_value_drift(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["profile"]["knobs"]["arm-metis"]["max_workers"] = 99
        with self.assertRaisesRegex(ValueError, "profile knobs"):
            REPORT.build_summary_from_sources(broken)

    def test_builder_rejects_extra_stage6_count_tool(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["counts"]["per_tool"]["unexpected-tool"] = {"unique": 0, "stable": 0}
        with self.assertRaisesRegex(ValueError, "Stage 6 count tools"):
            REPORT.build_summary_from_sources(broken)

    def test_builder_rejects_contradictory_quality_gate_check(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["final"]["per_tool"]["arm-metis"]["quality_gate"]["checks"]["precision"] = False
        with self.assertRaisesRegex(ValueError, "quality gate checks"):
            REPORT.build_summary_from_sources(broken)

    def test_builder_rejects_contradictory_resource_gate_flags(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["final"]["per_tool"]["arm-metis"]["resource_gate"]["improved"] = False
        with self.assertRaisesRegex(ValueError, "resource gate improved"):
            REPORT.build_summary_from_sources(broken)

    def test_builder_rejects_contradictory_resource_gate_bounded_flag(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["final"]["per_tool"]["arm-metis"]["resource_gate"]["bounded"] = False
        with self.assertRaisesRegex(ValueError, "resource gate bounded"):
            REPORT.build_summary_from_sources(broken)

    def test_builder_rejects_contradictory_quality_gate_passed_flag(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["final"]["per_tool"]["arm-metis"]["quality_gate"]["passed"] = False
        with self.assertRaisesRegex(ValueError, "quality gate passed"):
            REPORT.build_summary_from_sources(broken)

    def test_builder_rejects_contradictory_budget_gate(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["final"]["budget_passed"] = False
        with self.assertRaisesRegex(ValueError, "final budget passed"):
            REPORT.build_summary_from_sources(broken)

    def test_builder_rejects_incomplete_recall_artifact(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["recall_baseline"]["per_tool"]["arm-metis"]["lessons_hit_cwe"] = []
        with self.assertRaisesRegex(ValueError, "recall strict lessons arm-metis"):
            REPORT.build_summary_from_sources(broken)

    def test_builder_rejects_recall_granularity_mismatch_with_config(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        broken["config"]["ground_truth"]["granularity"] = "finding"
        with self.assertRaisesRegex(ValueError, "recall granularity config"):
            REPORT.build_summary_from_sources(broken)

    def test_recall_validation_uses_stage7b_same_model_provenance(self):
        sources = REPORT.load_sources(ROOT)
        expected = {
            lesson: cwe
            for lesson, cwe in sources["config"]["ground_truth"]["lessons"].items()
            if cwe
        }
        sources["judged_independent"] = []
        REPORT._validate_recall(sources, expected, ["arm-metis", "datadog-saist"])

    def test_recall_validation_rejects_same_model_conflict(self):
        sources = REPORT.load_sources(ROOT)
        broken = copy.deepcopy(sources)
        row = next(
            item
            for item in broken["judged_same"]
            if item["tool"] == "arm-metis"
            and "/lessons/htmltampering/" in item["file"]
            and item["verdict"] == "TP"
            and item.get("judge_cwe") == "CWE-602"
        )
        row["verdict"] = "FP"
        expected = {
            lesson: cwe
            for lesson, cwe in broken["config"]["ground_truth"]["lessons"].items()
            if cwe
        }
        with self.assertRaisesRegex(ValueError, "recall strict lessons arm-metis"):
            REPORT._validate_recall(
                broken, expected, ["arm-metis", "datadog-saist"]
            )


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

    def test_markdown_handles_same_winner_without_hard_coded_tool_claims(self):
        summary = copy.deepcopy(self.summary)
        winner = "datadog-saist"
        summary["recommendations"] = {
            "deep_review": {"tool": winner, "reason_metrics": []},
            "ci_gate": {"tool": winner, "reason_metrics": []},
            "combined": {
                "schedule": {"per_pr": winner, "nightly_or_release": winner}
            },
            "universal_winner": None,
        }
        text = REPORT.render_markdown(summary)
        self.assertIn("không chọn scanner thứ hai", text)
        self.assertIn(f"`{winner}`", text)
        self.assertNotIn("Hai tool bổ sung nhau", text)
        self.assertNotIn("Finding từ Metis", text)

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

    def test_check_outputs_detects_crlf_as_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            REPORT.write_outputs(root, self.summary)
            summary_path = root / "results" / "report" / "final-summary.json"
            summary_path.write_bytes(
                REPORT.canonical_json(self.summary).replace("\n", "\r\n").encode("utf-8")
            )
            with self.assertRaisesRegex(ValueError, "stale generated output"):
                REPORT.check_outputs(root, self.summary)


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
