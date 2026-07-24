import importlib.util
import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "stage7_judge", ROOT / "scripts" / "stage7_judge.py"
)
JUDGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(JUDGE)


def finding(title):
    return {
        "tool": "arm-metis",
        "file": "src/main/java/Example.java",
        "line_min": 10,
        "title": title,
    }


class CheckpointTests(unittest.TestCase):
    def test_resume_skips_findings_already_in_checkpoint(self):
        done = finding("already judged")
        done["verdict"] = "TP"
        pending = finding("still pending")

        with TemporaryDirectory() as tmp:
            checkpoint = Path(tmp) / "judged-pro.jsonl"
            checkpoint.write_text(json.dumps(done) + "\n", encoding="utf-8")

            completed = JUDGE.load_checkpoint(checkpoint)
            remaining = JUDGE.remaining_records([done, pending], completed)

        self.assertEqual([pending], remaining)


class WorkerTests(unittest.TestCase):
    def test_map_records_checkpoints_completed_work_without_head_of_line_blocking(self):
        release_first = threading.Event()

        def work(value):
            if value == 0:
                release_first.wait(timeout=2)
                time.sleep(0.1)
            else:
                release_first.set()
            return value

        results = list(JUDGE.map_records([0, 1], work, workers=2))

        self.assertEqual([1, 0], results)

    def test_map_records_does_not_run_the_entire_queue_after_fatal_error(self):
        calls = []

        def work(value):
            calls.append(value)
            if value == 0:
                time.sleep(0.05)
                raise RuntimeError("fatal")
            time.sleep(0.2)
            return value

        with self.assertRaisesRegex(RuntimeError, "fatal"):
            list(JUDGE.map_records(range(10), work, workers=2))

        self.assertLessEqual(len(calls), 2)


class RequestTests(unittest.TestCase):
    def test_request_body_includes_opt_in_reasoning_effort(self):
        body = JUDGE.build_request_body("prompt", "judge-alias", "low")

        self.assertEqual("judge-alias", body["model"])
        self.assertEqual("low", body["reasoning_effort"])
        self.assertNotIn(
            "reasoning_effort",
            JUDGE.build_request_body("prompt", "judge-alias", None),
        )

    def test_proxy_allows_reasoning_effort_for_pro_judge(self):
        config = yaml.safe_load(
            (ROOT / "proxy" / "litellm_config.yaml").read_text(encoding="utf-8")
        )
        pro = next(
            item for item in config["model_list"]
            if item["model_name"] == "gemini-31-pro-judge"
        )

        self.assertIn(
            "reasoning_effort",
            pro["litellm_params"]["allowed_openai_params"],
        )

    def test_proxy_has_low_reasoning_independent_fallback_judge(self):
        config = yaml.safe_load(
            (ROOT / "proxy" / "litellm_config.yaml").read_text(encoding="utf-8")
        )
        fallback = next(
            item for item in config["model_list"]
            if item["model_name"] == "gemini-3-flash-judge"
        )

        self.assertEqual(
            "gemini/gemini-3-flash-preview",
            fallback["litellm_params"]["model"],
        )
        self.assertIn(
            "reasoning_effort",
            fallback["litellm_params"]["allowed_openai_params"],
        )
        self.assertEqual(0, fallback["litellm_params"]["num_retries"])

    def test_daily_model_quota_is_fatal_but_short_rate_limit_is_retryable(self):
        daily = "Quota exceeded: generate_requests_per_model_per_day; retry in 21h"
        rpm = "Quota exceeded: generate_content_requests_per_minute; retry in 30s"

        self.assertTrue(JUDGE.is_daily_quota_error(429, daily))
        self.assertFalse(JUDGE.is_daily_quota_error(429, rpm))
        self.assertFalse(JUDGE.is_daily_quota_error(503, daily))

    @mock.patch.object(JUDGE, "build_context", return_value=("code", "file", ""))
    @mock.patch.object(
        JUDGE,
        "call_judge",
        return_value='{"verdict":"FP","confidence":"high","cwe":null,"reason":"x"}',
    )
    def test_judge_record_passes_explicit_retry_budget(self, call, _context):
        rec = finding("finding")
        rec.update({"cwe": None, "message": "reason"})

        result, error = JUDGE.judge_record(
            rec, "target", {}, "http://proxy", "key", "alias",
            reasoning_effort="low", retries=1,
        )

        self.assertIsNone(error)
        self.assertEqual("FP", result["verdict"])
        self.assertEqual(1, call.call_args.kwargs["retries"])


if __name__ == "__main__":
    unittest.main()
