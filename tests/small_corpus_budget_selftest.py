"""Exercise original render call admission at small and normal corpus sizes."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
import config
from public_stage_material_selftest import FakeTracer, world
from pipeline.render import render_corpus
from pipeline.corpus_contract import validate_corpus


class FullLengthFillerTracer(FakeTracer):
    def chat_text(self, step, messages, **kwargs):
        super().chat_text(step, messages, **kwargs)
        return "外围仓库档案整理。" * 120


class SmallCorpusBudgetTests(unittest.TestCase):
    def test_negative_target_fails_before_any_model_call(self):
        tracer = FullLengthFillerTracer()
        with self.assertRaisesRegex(ValueError, "不能为负数"):
            render_corpus({}, world(), -1, tracer, {"sessions": []}, set(), lambda: None)
        self.assertEqual(tracer.calls, [])

    def test_small_requests_reduce_noise_calls_without_losing_world_or_rule_documents(self):
        results = []
        with patch.object(config, "pmap", side_effect=lambda fn, items, **kw: [fn(i) for i in items]), \
             patch.object(config, "chat", side_effect=AssertionError("network forbidden")), \
             patch.object(config, "chat_json", side_effect=AssertionError("network forbidden")):
            for target, expected_noise_calls in ((0, 0), (1600, 2), (12800, 16)):
                ws, tracer, corpus = world(), FullLengthFillerTracer(), {"sessions": []}
                before = deepcopy(ws.to_dict())
                render_corpus({"domain_profile": {}, "quality_contract": {"corpus_review": True}},
                    ws, target, tracer, corpus, set(), lambda: None, log=lambda *_: None)
                self.assertEqual(sum(step == "render.filler" for step, _ in tracer.calls), expected_noise_calls)
                self.assertEqual(validate_corpus(ws, corpus)["status"], "passed")
                self.assertEqual(ws.to_dict(), before)
                results.append([(s["session_id"], d["content"]) for s in corpus["sessions"]
                                for d in s["docs"] if not d.get("is_filler")])
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])


if __name__ == "__main__":
    unittest.main()
