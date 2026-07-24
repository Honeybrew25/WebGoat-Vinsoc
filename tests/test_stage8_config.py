import importlib.util
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "bench_config", ROOT / "scripts" / "bench_config.py"
)
CFG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CFG)


class Stage8ConfigTests(unittest.TestCase):
    def test_balanced_profile_has_approved_knobs_and_gates(self):
        cfg = CFG.load()
        profile = CFG.stage8_profile(cfg, "balanced-v1")

        self.assertEqual(5.0, cfg["stage8"]["budget_usd"])
        self.assertEqual(12, profile["arm-metis"]["max_workers"])
        self.assertEqual("*.java", profile["arm-metis"]["review_include"])
        self.assertEqual(25, profile["datadog-saist"]["file_concurrency"])
        self.assertEqual(
            0.751,
            cfg["stage8"]["quality_floor"]["arm-metis"]["precision"],
        )

    def test_unknown_profile_is_rejected(self):
        with self.assertRaisesRegex(KeyError, "unknown stage8 profile"):
            CFG.stage8_profile(CFG.load(), "missing")

    def test_cli_emits_compact_tool_profile_json(self):
        result = subprocess.run(
            [
                "uv",
                "run",
                "--with",
                "pyyaml",
                "python",
                str(ROOT / "scripts" / "bench_config.py"),
                "stage8-profile",
                "balanced-v1",
                "arm-metis",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(12, json.loads(result.stdout)["max_workers"])


if __name__ == "__main__":
    unittest.main()
