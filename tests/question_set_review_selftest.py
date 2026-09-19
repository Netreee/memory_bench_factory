from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.question_set_review import review_question_set


class QuestionSetTests(unittest.TestCase):
    def review(self, mutate=None):
        out = {"assessment": "两题问同一事件。", "issues": [], "overlap_groups": [
            {"qids": ["a", "b"], "assessment": "重合但表达不同。", "suggested_response": "保留一题供下一步核查。"}],
            "coverage_observations": [], "limitations": ["没有测试系统答对率。"], "evidence": []}
        if mutate: mutate(out)
        self.calls = []
        def fake(step, messages, **params):
            self.calls.append((step, messages, params)); return deepcopy(out)
        return review_question_set([{"qid": "a", "question": "何时收到？", "gold": "hidden",
            "reference_proposal": {"answer": "昨天", "rationale": "记录"}},
            {"qid": "b", "question": "收到时间？"}],
            [{"content": "昨天收到。", "title": "hidden_title"}], "仅依据材料", model="model", chat_json=fake)

    def test_review_does_not_drop_or_approve(self):
        result = self.review()
        self.assertTrue(result["proposal_ready"])
        self.assertEqual(result["proposed_questions"], [])
        self.assertEqual(len(result["original"]["questions"]), 2)
        self.assertEqual(len(self.calls), 1)

    def test_inputs_projected(self):
        self.review()
        payload = self.calls[0][1][1]["content"]
        self.assertNotIn("hidden", payload)
        self.assertIn("reference_proposal", payload)

    def test_unknown_qid_rejected(self):
        result = self.review(lambda o: o["overlap_groups"][0].update(qids=["a", "unknown"]))
        self.assertFalse(result["proposal_ready"])

    def test_empty_groups_valid(self):
        result = self.review(lambda o: o.update(overlap_groups=[]))
        self.assertTrue(result["proposal_ready"])

    def test_quote_not_repaired(self):
        result = self.review(lambda o: o.update(evidence=[{"doc_id": "d000001", "field": "content",
            "quote": "今天收到。", "role": "support", "explanation": "错误", "location_scope": "quote"}]))
        self.assertFalse(result["proposal_ready"])
        self.assertEqual(result["raw_output"]["evidence"][0]["quote"], "今天收到。")


if __name__ == "__main__": unittest.main()
