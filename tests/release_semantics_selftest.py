"""End-to-end release regressions for typed aliases and numeric gold units.

The corpus reviewer is stubbed only to isolate the release/oracle contract.
No model call is made and no pre-existing run is modified.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.corpus_contract import review_documents, attach_receipts
from pipeline.question_contract import attach_question_contract, bind_question_world
from pipeline.quality import evaluate_release
from pipeline.world_state import WorldState, Timeline, Op, SET, UPDATE


class StubReviewer:
    def chat_json(self, *args, **kwargs):
        return {"verdict": "pass", "unsupported_claims": []}


def release_fixture(directory, wp, ws, order, values, *, bind=True):
    q = attach_question_contract(bind_question_world(order, ws) if bind else order, wp)
    q["question"] = q["question_contract"]["canonical_question"]
    sessions = []
    for session, value in enumerate(values):
        docs = [{"doc_id": f"doc{session}", "is_filler": False,
                 "content": f"测试报告的{order['field']}为{value}。"}]
        review = review_documents(StubReviewer(), ws, session, docs)
        attach_receipts(docs, review, session)
        sessions.append({"session_id": session, "docs": docs})
    final = {**q, "candidate_evidence_doc_ids": [f"doc{s}" for s in order["evidence_sessions"]],
             "evidence_doc_ids": [f"doc{s}" for s in order["evidence_sessions"]]}
    artifacts = {"01_whitepaper.json": wp, "02_world.json": ws.to_dict(), "04_questions.json": [q],
                 "06_grounded_questions.json": [final], "05_corpus.json": {"corpus": {"sessions": sessions}},
                 "00_about.json": {}, "manifest.json": {"status": "done", "algo": {}}}
    for name, content in artifacts.items():
        (directory / name).write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    return evaluate_release(directory), q


class ReleaseSemanticsTests(unittest.TestCase):
    def test_same_field_other_type_alias_cannot_pass_release(self):
        a = {"name": "状态", "kind": "status", "allowed_aliases": {"已登记": ["待接收"]}}
        b = {"name": "状态", "kind": "status", "allowed_aliases": {"已登记": []}}
        blueprint = {"entity_types": [{"id": "a", "fields": [a]}, {"id": "b", "fields": [b]}]}
        ws = WorldState({"测试报告": {"状态": Timeline([Op(0, "2025-01-06", SET, "待接收"),
                        Op(1, "2025-01-13", UPDATE, "已登记", "待接收")])}}, n_sessions=2,
                        entity_types={"测试报告": "b"}, world_blueprint=blueprint)
        wp = {"world_blueprint": blueprint, "active_lines": [{"line": "L1_timeline", "weight": 1}]}
        order = {"line": "L1_timeline", "capability": "KU", "entity": "测试报告", "field": "状态",
                 "gt": "已登记", "evidence_sessions": [1], "aux": {"ans_kind": "status", "value_schema": a},
                 "allowed_aliases": ["待接收"]}
        with TemporaryDirectory() as tmp:
            good, question = release_fixture(Path(tmp), wp, ws, order, ["待接收", "已登记"])
            self.assertTrue(good["eligible"], good["issues"])
            self.assertEqual(question["question_contract"]["allowed_aliases"], [])
            unbound, _ = release_fixture(Path(tmp), wp, ws, order, ["待接收", "已登记"], bind=False)
            self.assertFalse(unbound["eligible"])
            wrong_type = {**order, "entity_type": "a"}
            bad, _ = release_fixture(Path(tmp), wp, ws, wrong_type, ["待接收", "已登记"], bind=False)
            self.assertFalse(bad["eligible"])
            self.assertIn("question_contract_policy_mismatch", {item["code"] for item in bad["issues"]})

    def test_percent_corruption_fails_release_and_correct_number_passes(self):
        numeric = {"name": "系数", "kind": "numeric"}
        wp = {"domain_profile": {"field_schema": [numeric]}, "active_lines": [{"line": "L1_timeline", "weight": 1}]}
        ws = WorldState({"测试报告": {"系数": Timeline([Op(0, "2025-01-06", SET, "0.25"),
                        Op(1, "2025-01-13", UPDATE, "0.5", "0.25")])}}, n_sessions=2)
        order = {"line": "L1_timeline", "capability": "MR", "entity": "测试报告", "field": "系数",
                 "gt": {"value": "0.5", "session": 1, "date": "2025-01-13", "agg": "max"},
                 "aux": {"agg": "max", "ans_kind": "numeric", "value_schema": numeric}, "evidence_sessions": [0, 1]}
        with TemporaryDirectory() as tmp:
            good, _ = release_fixture(Path(tmp), wp, ws, order, ["0.25", "0.5"])
            self.assertTrue(good["eligible"], good["issues"])
            order["gt"]["value"] = "0.5%"
            bad, _ = release_fixture(Path(tmp), wp, ws, order, ["0.25", "0.5"])
            self.assertFalse(bad["eligible"])
            self.assertIn("invalid_question_plan", {item["code"] for item in bad["issues"]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
