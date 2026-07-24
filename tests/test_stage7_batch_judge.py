import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "stage7_batch_judge", ROOT / "scripts" / "stage7_batch_judge.py"
)
BATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BATCH)


def finding(title="finding"):
    return {
        "tool": "arm-metis",
        "file": "src/main/java/Example.java",
        "line_min": 10,
        "title": title,
        "cwe": "CWE-79",
        "message": "untrusted input reaches response",
    }


class BatchPayloadTests(unittest.TestCase):
    def test_resolve_paths_keeps_defaults_and_accepts_stage8_overrides(self):
        defaults = BATCH.resolve_paths(None, None, None)
        self.assertTrue(defaults[0].endswith("deduped.jsonl"))
        self.assertTrue(defaults[1].endswith("judged-independent.jsonl"))
        self.assertTrue(defaults[2].endswith("judge_batch_state.json"))

        custom = BATCH.resolve_paths(
            "results/optimization/p/stats/novel.jsonl",
            "results/optimization/p/stats/judged-novel.jsonl",
            "results/optimization/p/stats/judge-state.json",
        )

        self.assertTrue(custom[0].endswith("novel.jsonl"))
        self.assertTrue(custom[1].endswith("judged-novel.jsonl"))
        self.assertTrue(custom[2].endswith("judge-state.json"))

    def test_resolve_paths_rejects_repository_escape(self):
        with self.assertRaisesRegex(ValueError, "escapes repository"):
            BATCH.resolve_paths("../outside.jsonl", None, None)

    @mock.patch.object(BATCH.judge, "build_context", return_value=("code", "file", ""))
    def test_inline_request_pins_low_thinking_and_preserves_key(self, _context):
        rec = finding()

        item = BATCH.build_inline_request(rec, "target", {}, "low")

        self.assertEqual(
            "low",
            item["request"]["generation_config"]["thinking_config"]["thinking_level"],
        )
        self.assertEqual(list(BATCH.judge.record_key(rec)), item["metadata"]["key"])
        self.assertIn("untrusted input reaches response", item["request"]["contents"][0]["parts"][0]["text"])


class BatchResponseTests(unittest.TestCase):
    def test_batch_state_reads_long_running_operation_metadata(self):
        running = {"metadata": {"state": "BATCH_STATE_RUNNING"}}
        succeeded = {
            "done": True,
            "metadata": {
                "state": "BATCH_STATE_SUCCEEDED",
                "batchStats": {"successfulRequestCount": "1"},
            },
            "response": {
                "inlinedResponses": {"inlinedResponses": [{"response": {}}]},
            },
        }

        self.assertEqual("BATCH_STATE_RUNNING", BATCH.batch_state(running))
        self.assertEqual("BATCH_STATE_SUCCEEDED", BATCH.batch_state(succeeded))
        self.assertEqual(
            {"successfulRequestCount": "1"},
            BATCH.batch_resource(succeeded)["batchStats"],
        )
        self.assertIn("inlinedResponses", BATCH.batch_output(succeeded))

    def test_apply_responses_merges_valid_verdict_and_reports_item_errors(self):
        good = finding("good")
        bad = finding("bad")
        response = {
            "response": {
                "candidates": [{
                    "content": {"parts": [{"text": json.dumps({
                        "verdict": "TP", "confidence": "high",
                        "cwe": "CWE-79", "reason": "real flow",
                    })}]}
                }]
            }
        }

        merged, errors = BATCH.apply_batch_responses(
            [good, bad], [response, {"error": {"message": "busy"}}], {},
            alias="gemini-3-flash-judge", effort="low",
        )

        self.assertEqual("TP", merged[BATCH.judge.record_key(good)]["verdict"])
        self.assertEqual("gemini-3-flash-judge", merged[BATCH.judge.record_key(good)]["judge_alias"])
        self.assertEqual(1, len(errors))
        self.assertIn("busy", errors[0])

    def test_batch_usage_prices_thinking_as_output(self):
        responses = [{
            "response": {
                "usageMetadata": {
                    "promptTokenCount": 1000,
                    "candidatesTokenCount": 200,
                    "thoughtsTokenCount": 300,
                    "totalTokenCount": 1500,
                }
            }
        }]

        usage = BATCH.summarize_batch_usage(
            responses, "gemini-3-flash-preview"
        )

        self.assertEqual(1000, usage["input_tokens"])
        self.assertEqual(500, usage["output_tokens"])
        self.assertAlmostEqual(0.001, usage["estimated_cost_usd"])
        self.assertTrue(usage["usage_complete"])


if __name__ == "__main__":
    unittest.main()
