import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Stage8RunnerTests(unittest.TestCase):
    def test_runner_has_budget_and_isolated_output_guards(self):
        runner = ROOT / "scripts" / "stage8_run.sh"
        text = runner.read_text(encoding="utf-8")

        self.assertIn("results/optimization", text)
        self.assertIn("--budget-check", text)
        self.assertIn("--run-index", text)
        self.assertIn("BENCH_RESULTS_ROOT", text)
        self.assertNotIn('rm -rf "$ROOT_DIR/results/findings"', text)

    def test_runner_supports_approved_modes_and_profile_knobs(self):
        text = (ROOT / "scripts" / "stage8_run.sh").read_text(encoding="utf-8")

        for option in ("--dry-run", "--screen", "--complete", "--tool"):
            self.assertIn(option, text)
        for knob in (
            "METIS_MAX_WORKERS",
            "METIS_REVIEW_INCLUDE",
            "METIS_REVIEW_EXCLUDES",
            "SAIST_FILE_CONCURRENCY",
        ):
            self.assertIn(knob, text)


if __name__ == "__main__":
    unittest.main()
