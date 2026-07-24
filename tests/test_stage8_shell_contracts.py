import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Stage8ShellContractTests(unittest.TestCase):
    def test_adapters_expose_optional_stage8_knobs(self):
        metis = (ROOT / "adapters" / "arm-metis.sh").read_text(encoding="utf-8")
        saist = (ROOT / "adapters" / "datadog-saist.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("METIS_MAX_WORKERS", metis)
        self.assertIn("METIS_REVIEW_INCLUDE", metis)
        self.assertIn("METIS_REVIEW_EXCLUDES", metis)
        self.assertIn("SAIST_FILE_CONCURRENCY", saist)

    def test_stage4_runner_supports_protected_output_and_one_run(self):
        runner = (ROOT / "scripts" / "stage4_run.sh").read_text(encoding="utf-8")

        self.assertIn("--output-root", runner)
        self.assertIn("--run-index", runner)
        self.assertIn("BENCH_RESULTS_ROOT", runner)
        self.assertIn("results/optimization", runner)


if __name__ == "__main__":
    unittest.main()
