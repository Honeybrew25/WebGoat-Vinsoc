import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "stage7c_judge_agreement",
    ROOT / "scripts" / "stage7c_judge_agreement.py",
)
AGREEMENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AGREEMENT)


class AgreementMetricTests(unittest.TestCase):
    def test_cohens_kappa_removes_chance_agreement(self):
        metrics = AGREEMENT.agreement_metrics(
            ["TP", "TP", "FP", "FP"],
            ["TP", "FP", "FP", "FP"],
        )

        self.assertEqual(4, metrics["n"])
        self.assertEqual(0.75, metrics["agreement"])
        self.assertEqual(0.5, metrics["cohens_kappa"])

    def test_agreement_metrics_rejects_empty_or_misaligned_inputs(self):
        with self.assertRaises(ValueError):
            AGREEMENT.agreement_metrics([], [])
        with self.assertRaises(ValueError):
            AGREEMENT.agreement_metrics(["TP"], ["TP", "FP"])


if __name__ == "__main__":
    unittest.main()
