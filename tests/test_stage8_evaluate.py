import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "stage8_evaluate", ROOT / "scripts" / "stage8_evaluate.py"
)
EVAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVAL)


def rec(line=10, verdict="TP", exact=True):
    return {
        "tool": "arm-metis",
        "file": "src/A.java",
        "title": "SQL Injection",
        "line_min": line,
        "line_confidence": "exact" if exact else "unreliable",
        "verdict": verdict,
        "run_count": 3,
        "judge_cwe": "CWE-89",
        "cwe": "CWE-89",
    }


class Stage8EvaluateTests(unittest.TestCase):
    def test_match_baseline_uses_title_and_line_tolerance(self):
        self.assertEqual(
            "TP",
            EVAL.match_baseline(rec(17), [rec(10)], 10)["verdict"],
        )
        self.assertIsNone(EVAL.match_baseline(rec(21), [rec(10)], 10))

    def test_unreliable_line_does_not_block_same_finding(self):
        self.assertEqual(
            "TP",
            EVAL.match_baseline(rec(200, exact=False), [rec(10)], 10)[
                "verdict"
            ],
        )

    def test_budget_keeps_judge_reserve(self):
        self.assertTrue(EVAL.budget_allows(3.5, 1.2, 0.25, 5.0))
        self.assertFalse(EVAL.budget_allows(3.7, 1.2, 0.25, 5.0))

    def test_projected_run_cost_uses_matching_baseline_run(self):
        rows = [
            {"tool": "arm-metis", "run": "run-01-cold", "cost_usd": 1.3},
            {"tool": "arm-metis", "run": "run-02-warm", "cost_usd": 0.8},
            {"tool": "arm-metis", "run": "run-03-warm", "cost_usd": 0.9},
        ]

        self.assertEqual(1.3, EVAL.projected_run_cost(rows, "arm-metis", 1))
        self.assertEqual(0.8, EVAL.projected_run_cost(rows, "arm-metis", 2))

    def test_judge_spend_requires_complete_usage_for_novel_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            stats = Path(tmp)
            state = {
                "collected": True,
                "usage": {"usage_complete": True},
                "estimated_cost_usd": 0.0123,
            }
            (stats / "judge-state.json").write_text(
                json.dumps(state), encoding="utf-8"
            )

            self.assertEqual(0.0123, EVAL.judge_spend(stats, [rec(30)]))
            with self.assertRaisesRegex(ValueError, "usage is incomplete"):
                state["usage"]["usage_complete"] = False
                (stats / "judge-state.json").write_text(
                    json.dumps(state), encoding="utf-8"
                )
                EVAL.judge_spend(stats, [rec(30)])

    def test_resource_gate_needs_gain_without_resource_regression(self):
        baseline = {
            "wall_clock_s": 100,
            "total_tokens": 1000,
            "cost_usd": 1.0,
        }

        self.assertTrue(
            EVAL.resource_gate(
                baseline,
                {"wall_clock_s": 88, "total_tokens": 1010, "cost_usd": 1.01},
                0.10,
                0.05,
            )["passed"]
        )
        self.assertFalse(
            EVAL.resource_gate(
                baseline,
                {"wall_clock_s": 88, "total_tokens": 1060, "cost_usd": 1.0},
                0.10,
                0.05,
            )["passed"]
        )

    def test_quality_gate_applies_tool_specific_floors(self):
        floors = {"precision": 0.751, "recall_lessons": 12, "stable_tp": 144}

        self.assertTrue(
            EVAL.quality_gate(
                {"precision": 0.76, "recall_lessons": 12, "stable_tp": 144},
                floors,
            )["passed"]
        )
        self.assertFalse(
            EVAL.quality_gate(
                {"precision": 0.75, "recall_lessons": 12, "stable_tp": 144},
                floors,
            )["passed"]
        )

    def test_screening_counts_novel_finding_as_false_positive(self):
        inherited = EVAL.inherit_or_conservative_fp(rec(10), [rec(10)], 10)
        novel = EVAL.inherit_or_conservative_fp(rec(30), [rec(10)], 10)

        self.assertEqual("TP", inherited["verdict"])
        self.assertEqual("baseline-independent", inherited["verdict_source"])
        self.assertEqual("FP", novel["verdict"])
        self.assertEqual("screening-conservative", novel["verdict_source"])

    def test_prepare_novel_judge_keeps_only_unmatched_findings(self):
        inherited = rec(10)
        novel = rec(30)
        inherited.pop("verdict")
        novel.pop("verdict")

        result = EVAL.prepare_novel_rows([inherited, novel], [rec(10)], 10)

        self.assertEqual([30], [row["line_min"] for row in result])

    def test_merge_final_verdicts_combines_inherited_and_novel(self):
        inherited = rec(10)
        novel = rec(30)
        inherited.pop("verdict")
        novel.pop("verdict")
        judged_novel = {**novel, "verdict": "TP", "judge_cwe": "CWE-89"}

        merged = EVAL.merge_final_verdicts(
            [inherited, novel], [rec(10)], [judged_novel], 10
        )

        self.assertEqual(["TP", "TP"], [row["verdict"] for row in merged])
        self.assertEqual(
            ["baseline-independent", "novel-independent"],
            [row["verdict_source"] for row in merged],
        )

    def test_merge_final_verdicts_rejects_missing_novel_verdict(self):
        novel = rec(30)
        novel.pop("verdict")

        with self.assertRaisesRegex(ValueError, "missing novel verdict"):
            EVAL.merge_final_verdicts([novel], [rec(10)], [], 10)


if __name__ == "__main__":
    unittest.main()
