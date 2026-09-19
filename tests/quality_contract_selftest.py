"""Offline release, claim, stale-artifact and review-schema regression tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OPENAI_API_KEY", "offline-audit")
os.environ.setdefault("MODEL", "offline-audit")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_fixture_helpers import fixed_document_reviews
from pipeline.corpus_contract import (canonical_context, explicit_future_claims,
                                      review_documents, attach_receipts, validate_corpus, fidelity_requirements)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_fixture_helpers import fixed_positive_review
from pipeline.quality import (evaluate_release, quality_snapshot, require_release, ReleaseError)
from pipeline.question_contract import attach_question_contract
from pipeline.world_state import WorldState, Timeline, Op, SET, UPDATE


class Reviewer:
    def __init__(self, response=None):
        self.response = response
        self.calls = 0

    def chat_json(self, *args, **kwargs):
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return fixed_positive_review(args[1]) if self.response is None else self.response


def world():
    return WorldState(entities={"测试报告": {"状态": Timeline([
        Op(0, "2025-01-06", SET, "待接收"), Op(1, "2025-01-13", UPDATE, "已登记", "待接收")])}}, n_sessions=2)


def fixture(directory):
    ws = world()
    wp = {"active_lines": [{"line": "L1_timeline", "weight": 1}],
          "domain_profile": {"field_schema": [{"name": "状态", "kind": "status"}]}}
    q = attach_question_contract({"line": "L1_timeline", "capability": "KU", "entity": "测试报告",
                                  "field": "状态", "gt": "已登记", "evidence_sessions": [1],
                                  "aux": {"at_week": 1, "ans_kind": "status", "time_unit": "周"}}, wp)
    q["question"] = q["question_contract"]["canonical_question"]
    corpus = {"sessions": []}
    for sid, value in enumerate(("待接收", "已登记")):
        docs = [{"doc_id": f"s{sid}", "content": f"测试报告的状态为{value}。", "is_filler": False}]
        report = review_documents(Reviewer(), ws, sid, docs, requirements=fidelity_requirements(ws, sid))
        attach_receipts(docs, report, sid)
        corpus["sessions"].append({"session_id": sid, "docs": docs})
    payloads = {"01_whitepaper.json": wp, "02_world.json": ws.to_dict(), "04_questions.json": [q],
                "05_corpus.json": {"corpus": corpus, "done_weeks": [0, 1]},
                "06_grounded_questions.json": [{**q, "evidence_doc_ids": ["s1"]}],
                "00_about.json": {"answer_protocol": {"version": 5}},
                "manifest.json": {"status": "done", "algo": {}}}
    for name, value in payloads.items():
        (directory / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return ws, corpus


class ContractTests(unittest.TestCase):
    def test_future_state_and_valid_time(self):
        self.assertEqual(explicit_future_claims(world(), 0, "测试报告状态为已登记。")[0]["code"], "future_fact_asserted")
        self.assertEqual(explicit_future_claims(world(), 1, "测试报告状态为已登记。"), [])

    def test_parenthetical_reference_does_not_change_subject(self):
        ws = world()
        ws.entities["另一个客户"] = {}
        text = "测试报告（所属客户=另一个客户；金额=85）完成登记，状态为已登记。"
        self.assertEqual(explicit_future_claims(ws, 0, text)[0]["entity"], "测试报告")

    def test_prediction_and_negation_are_not_future_assertions(self):
        for text in ("测试报告预计下周状态为已登记。", "测试报告状态尚未变为已登记。"):
            self.assertEqual(explicit_future_claims(world(), 0, text), [])

    def test_generation_review_context_excludes_future_values(self):
        context = canonical_context(world(), 0)
        self.assertNotIn("已登记", json.dumps(context, ensure_ascii=False))
        self.assertEqual(context["document_date"], "2025-01-06")

    def test_real_insurance_future_example(self):
        # Public synthetic record, independently reduced to the offending state.
        name = "玄磁材料2023年半年度财务披露文件"
        ws = WorldState(entities={name: {"资料接收状态": Timeline([Op(1, "2025-01-13", SET, "已登记")])},
                                  "澜渡冷链物流集团": {}}, n_sessions=4)
        text = (name + "（报告期间=2023年半年度；来源发布日期=2023-08-01；版本性质=草稿；"
                "来源客户=澜渡冷链物流集团；税后利润=85）亦完成资料接收登记，状态为已登记。")
        self.assertEqual(explicit_future_claims(ws, 0, text)[0]["first_observed_session"], 1)

    def test_failed_review_cannot_certify_documents(self):
        tracer = Reviewer({"document_reviews": fixed_document_reviews([{}], unsupported_indices=[0]), "verdict": "fail", "unsupported_claims": [
            {"doc_index": 0, "quote": "测试报告状态为已登记。", "reason": "截至本期只有待接收记录"}], "coverage": []})
        docs = [{"content": "测试报告状态为已登记。"}]
        result = review_documents(tracer, world(), 0, docs)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(tracer.calls, 1)
        with self.assertRaises(ValueError):
            attach_receipts(docs, result, 0)

    def test_reviewer_error_and_invalid_schema_are_not_pass(self):
        for response in (TimeoutError("offline timeout"), {}, {"verdict": "pass", "unsupported_claims": ["bad"]},
                         {"verdict": "fail", "unsupported_claims": [{"doc_index": 0, "quote": "invented", "reason": "x"}]}):
            result = review_documents(Reviewer(response), world(), 0, [{"content": "测试报告状态为待接收。"}])
            self.assertEqual(result["status"], "error")

    def test_document_edits_invalidate_review(self):
        with tempfile.TemporaryDirectory() as td:
            ws, corpus = fixture(Path(td))
            self.assertEqual(validate_corpus(ws, corpus)["status"], "passed")
            corpus["sessions"][0]["docs"][0]["content"] += "附加内容。"
            self.assertIn("missing_or_stale_document_review", {i["code"] for i in validate_corpus(ws, corpus)["issues"]})

    def test_title_edits_and_review_transfer_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            ws, corpus = fixture(Path(td))
            doc = corpus["sessions"][0]["docs"][0]
            doc["title"] = "另外一个标题"
            self.assertIn("missing_or_stale_document_review", {i["code"] for i in validate_corpus(ws, corpus)["issues"]})
        original = [{"content": "测试报告状态为待接收。"}]
        report = review_documents(Reviewer(), world(), 0, original)
        with self.assertRaises(ValueError):
            attach_receipts([{"content": "另一份文档"}], report, 0)

    def test_legitimate_replacement_document_cannot_preserve_old_grounding(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            ws, corpus = fixture(directory)
            docs = corpus["sessions"][1]["docs"]
            docs[0]["content"] = "本期无新增信息。"
            attach_receipts(docs, review_documents(Reviewer(), ws, 1, docs), 1)
            (directory / "05_corpus.json").write_text(json.dumps({"corpus": corpus}), encoding="utf-8")
            report = evaluate_release(directory)
            self.assertFalse(report["eligible"])
            self.assertIn("current_corpus_grounding_failed", {i["code"] for i in report["issues"]})

    def test_conflict_flag_cannot_bypass_review(self):
        corpus = {"sessions": [{"session_id": sid, "docs": [{"doc_id": f"x{sid}", "is_conflict": True,
                                                              "content": "捏造的正文"}]} for sid in (0, 1)]}
        self.assertIn("unverified_conflict_document", {i["code"] for i in validate_corpus(world(), corpus)["issues"]})

    def test_release_then_input_tamper(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            fixture(directory)
            report = evaluate_release(directory)
            self.assertTrue(report["eligible"], report["issues"])
            (directory / "07_release.json").write_text(json.dumps(report), encoding="utf-8")
            self.assertTrue(require_release(directory / "06_grounded_questions.json")["eligible"])
            (directory / "05_corpus.json").write_text("{}", encoding="utf-8")
            self.assertEqual(quality_snapshot(directory)["status"], "stale")
            with self.assertRaises(ReleaseError):
                require_release(directory / "06_grounded_questions.json")

    def test_missing_release_requires_explicit_research_override(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "06_grounded_questions.json"
            self.assertEqual(quality_snapshot(td)["status"], "not_run")
            with self.assertRaises(ReleaseError):
                require_release(path)
            self.assertTrue(require_release(path, allow_unverified=True)["override"])

    def test_manual_rejection_beats_old_pass(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            fixture(directory)
            (directory / "07_release.json").write_text(json.dumps(evaluate_release(directory)), encoding="utf-8")
            (directory / "08_semantic_review.json").write_text('{"review_status":"manual_failed"}', encoding="utf-8")
            self.assertEqual(quality_snapshot(directory)["status"], "failed")
            self.assertFalse(quality_snapshot(directory)["eligible"])

    def test_delivery_targets_are_bound_but_execution_status_is_not(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            fixture(directory)
            (directory / "07_release.json").write_text(json.dumps(evaluate_release(directory)), encoding="utf-8")
            manifest = {"status": "done", "llm_calls": 100, "algo": {}}
            (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertTrue(quality_snapshot(directory)["eligible"])
            manifest["algo"]["targetspec"] = {"min_questions": 100, "per_line_min": {"L1_timeline": 100}}
            (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(quality_snapshot(directory)["status"], "stale")

    def test_malformed_receipt_never_promotes_or_breaks_ui(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            fixture(directory)
            original = evaluate_release(directory)
            for key, bad in (("scope", None), ("checks", []), ("issues", None), ("eligible", "true")):
                (directory / "07_release.json").write_text(json.dumps({**original, key: bad}), encoding="utf-8")
                state = quality_snapshot(directory)
                self.assertFalse(state["eligible"])
                self.assertIsInstance(state["scope"], list)

    def test_resume_rechecks_stale_quality_without_force(self):
        from pipeline import run as run_module
        from pipeline.factory import STAGES
        with tempfile.TemporaryDirectory() as td, patch.object(run_module, "RUNS_DIR", Path(td)):
            run = run_module.Run("test", "quality_resume")
            fixture(run.dir)
            (run.dir / "manifest.json").write_text(json.dumps(run.manifest), encoding="utf-8")
            run_module.drive(run, STAGES, only="quality")
            first_attempt = run.manifest["stages"]["quality"]["attempt"]
            (run.dir / "00_about.json").write_text('{"answer_protocol":{"version":6}}', encoding="utf-8")
            self.assertEqual(quality_snapshot(run.dir)["status"], "stale")
            run_module.drive(run, STAGES, only="quality")
            self.assertEqual(run.manifest["stages"]["quality"]["attempt"], first_attempt + 1)
            self.assertTrue(quality_snapshot(run.dir)["eligible"])


if __name__ == "__main__":
    import socket
    def blocked(*args, **kwargs):
        raise RuntimeError("Network disabled in offline quality tests")
    socket.socket.connect = blocked
    unittest.main()
