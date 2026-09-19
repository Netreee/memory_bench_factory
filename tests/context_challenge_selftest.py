"""Offline behavior checks for bounded context trials and optional interpretation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.context_challenge import (prepare_challenge, run_challenge,
    prepare_collection_evidence, validate_collection_evidence, STEP)
from pipeline.quality_workflow import snapshot
from pipeline.question_set_review import review_question_set, SYSTEM, VERSION, MEASURED_VERSION


class ContextChallengeTests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(socket, "socket", side_effect=AssertionError("No network in unit tests"))
        self.network.start(); self.addCleanup(self.network.stop)
        self.docs = [
            {"doc_id": "d000001", "session": 1, "date": "2026-01-01", "content": "原承诺周五交付。"},
            {"doc_id": "d000002", "session": 2, "date": "2026-01-08", "content": "周六已交付；未说明之前发生了什么。"}]
        self.questions = [{"qid": "q1", "question": "能否判定逾期？", "reference_proposal": {
            "answer": {"private_reference": "NEVER_SHOW_REFERENCE"}, "rationale": "private rationale"}},
            {"qid": "q2", "question": "目前是否交付？", "reference_proposal": {"answer": "已交付", "rationale": "当前记录"}}]
        self.snap = snapshot(self.questions, self.docs, "仅依当前材料；缺证据可说明未知。")
        self.trials = [{"id": "one", "qid": "q1", "doc_ids": ["d000002"], "reason": "PRIVATE_DIAGNOSTIC_PURPOSE"},
                       {"id": "two", "qid": "q2", "doc_ids": ["d000002", "d000001"], "reason": "第二个未验证目的"}]
        self.calls = []
        self.raw = {"answer": "材料没有给原期限，无法判断是否逾期。", "extra_explanation": "Preserved extra field"}

    def call(self, step, messages, **params):
        self.calls.append((step, deepcopy(messages), deepcopy(params)))
        return deepcopy(self.raw)

    def run_report(self, **overrides):
        kwargs = dict(model="fixture", chat_json=self.call, max_calls=2)
        kwargs.update(overrides)
        return run_challenge(self.snap, self.trials, **kwargs)

    def collection(self, evidence=None, *, omit_evidence=False, **kw):
        def call(step, messages, **params):
            self.calls.append((step, deepcopy(messages), deepcopy(params)))
            return {"assessment": "材料不足是范围限制，不作分数。", "issues": [], "overlap_groups": [],
                "coverage_observations": [], "limitations": ["单次有限诊断"], "evidence": []}
        if not omit_evidence:
            kw["context_challenge_evidence"] = evidence
        return review_question_set(self.questions, self.docs, self.snap["protocol"], model="fixture",
            chat_json=call, **kw)

    def test_prepare_no_calls_public_only_exact_existing_wire_and_source_order(self):
        from tools.run_agent_evaluation import ANSWER_SYSTEM
        from eval.public_context import build_full_context
        original = deepcopy((self.snap, self.trials))
        prepared = prepare_challenge(self.snap, self.trials, model="fixture")
        self.assertEqual(self.calls, [])
        for row in prepared["trials"]:
            payload = json.loads(row["messages"][1]["content"])
            self.assertEqual(set(payload), {"public_question", "public_material", "public_protocol"})
            self.assertEqual(row["messages"][0]["content"], ANSWER_SYSTEM)
            self.assertNotIn("NEVER_SHOW_REFERENCE", str(row["messages"]))
            self.assertNotIn("PRIVATE_DIAGNOSTIC_PURPOSE", str(row["messages"]))
        self.assertEqual(prepared["trials"][1]["doc_ids"], ["d000001", "d000002"])
        self.assertEqual(json.loads(prepared["trials"][1]["messages"][1]["content"])["public_material"],
            build_full_context([(d["session"], d["date"], d["content"]) for d in self.docs])[0])
        self.assertEqual((self.snap, self.trials), original)

    def test_reject_bad_ids_doc_duplicates_and_unknown_before_any_call(self):
        for mutate in (lambda t: t[1].update(id="one"), lambda t: t[0].update(qid="missing"),
                       lambda t: t[0].update(doc_ids=["d000002", "d000002"]),
                       lambda t: t[0].update(doc_ids=["missing"]), lambda t: t[0].update(qid=[])):
            trials = deepcopy(self.trials); mutate(trials)
            with self.assertRaises(ValueError):
                run_challenge(self.snap, trials, model="fixture", chat_json=self.call, max_calls=2)
        self.assertEqual(self.calls, [])

    def test_input_limits_refuse_all_before_dispatch_without_truncation(self):
        for kw in ({"max_input_chars": 1}, {"full_context_char_budget": 1}):
            with self.assertRaises(ValueError): self.run_report(**kw)
        self.assertEqual(self.calls, [])

    def test_unknown_extra_fields_and_detached_callbacks_are_normal_results(self):
        before = deepcopy(self.snap)
        def on_trial(row): row.update(answer="callback mutation", doc_ids=[])
        report = self.run_report(on_trial=on_trial, solver_identity={"profile": "fixture"})
        self.assertEqual(report["execution"]["status"], "completed")
        self.assertEqual(report["calls_used"], 2)
        self.assertEqual(self.snap, before)
        for row in report["trials"]:
            self.assertEqual(row["raw_output"], self.raw)
            self.assertEqual(row["answer"], self.raw["answer"])
        self.assertTrue(report["solver_identity_bound"])
        self.assertTrue(all(c[0] == STEP and c[2]["retries"] == 1 and c[2]["top_p"] == 1.0 for c in self.calls))

    def test_zero_or_partial_budget_preserves_all_slots(self):
        for budget in (0, 1):
            self.calls = []
            report = self.run_report(max_calls=budget)
            self.assertEqual(len(self.calls), budget)
            self.assertEqual(report["execution"]["status"], "incomplete")
            self.assertEqual(len(report["trials"]), 2)
            self.assertEqual(report["trials"][-1]["execution"]["status"], "call_budget_exhausted")
            prepare_collection_evidence(self.snap, report)

    def test_provider_error_stops_one_without_relabeling_other_slot(self):
        def broken(step, messages, **params):
            self.calls.append(step); raise RuntimeError("synthetic quota failure")
        report = self.run_report(chat_json=broken)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(report["execution"]["status"], "failed")
        self.assertEqual(report["trials"][1]["execution"]["status"], "skipped_after_failure")
        self.assertIsNone(report["trials"][1]["answer"])
        prepare_collection_evidence(self.snap, report)

    def test_invalid_raw_shape_retained_and_no_second_dispatch(self):
        self.raw = {"answer": {"wrong": "shape"}, "reason": "retained"}
        report = self.run_report()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(report["trials"][0]["raw_output"], self.raw)
        self.assertEqual(report["trials"][0]["execution"]["status"], "invalid_output")
        prepare_collection_evidence(self.snap, report)

    def test_record_failure_before_call_or_after_raw_stops_and_retains_audit(self):
        for event, expected_calls in (("started", 0), ("finished", 1)):
            self.calls = []
            def record(row):
                if row["event"] == event: raise OSError("synthetic audit write failure")
            report = self.run_report(record=record)
            self.assertEqual(len(self.calls), expected_calls)
            self.assertEqual(report["execution"]["status"], "failed")
            self.assertEqual(report["trials"][0]["execution"]["status"], "audit_error")
            self.assertTrue(report["records"])
            if expected_calls: self.assertEqual(report["trials"][0]["raw_output"], self.raw)
            prepare_collection_evidence(self.snap, report)

    def test_trial_callback_failure_keeps_raw_and_stops(self):
        def fail(_): raise OSError("synthetic checkpoint failure")
        report = self.run_report(on_trial=fail)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(report["trials"][0]["raw_output"], self.raw)
        self.assertEqual(report["trials"][1]["execution"]["status"], "skipped_after_failure")
        prepare_collection_evidence(self.snap, report)

    def test_audit_failure_does_not_relax_raw_answer_or_record_binding(self):
        def fail(_): raise OSError("synthetic callback failure")
        def finished(event):
            if event["event"] == "finished": fail(event)
        for callback in ({"on_trial": fail}, {"record": finished}):
            report = self.run_report(**callback)
            prepare_collection_evidence(self.snap, report)
            for mutate in (
                    lambda r: r["trials"][0].update(answer="tampered"),
                    lambda r: r["trials"][0].update(answer=None),
                    lambda r: r["trials"][0]["raw_output"].update(extra_explanation="tampered"),
                    lambda r: r["records"][-1]["output"].update(answer="tampered"),
                    lambda r: r["records"][-1].update(call_index=99)):
                altered = deepcopy(report); mutate(altered)
                with self.assertRaises(ValueError): prepare_collection_evidence(self.snap, altered)

    def test_evidence_rejects_snapshot_report_or_projection_drift(self):
        report = self.run_report()
        evidence = prepare_collection_evidence(self.snap, report)
        altered = deepcopy(self.questions); altered[0]["reference_proposal"]["rationale"] += "changed"
        with self.assertRaises(ValueError): validate_collection_evidence(snapshot(altered, self.docs, self.snap["protocol"]), evidence)
        for mutate in (lambda e: e["observation"]["trials"][0].update(answer="tampered"),
                       lambda e: e["source_report"]["trials"][0].update(doc_ids=["d000001"]),
                       lambda e: e["source_report"]["trials"][0]["raw_output"].update(answer="tampered"),
                       lambda e: e["source_report"].update(calls_used=3)):
            changed = deepcopy(evidence); mutate(changed)
            with self.assertRaises(ValueError): validate_collection_evidence(self.snap, changed)

    def test_legacy_collection_wire_and_prompt_remain_exact(self):
        self.collection(omit_evidence=True)
        implicit = deepcopy(self.calls[-1])
        self.collection(None)
        self.assertEqual(implicit, self.calls[-1])
        self.assertEqual(hashlib.sha256(implicit[1][0]["content"].encode()).hexdigest(),
                         "cc2c364ce53eac9519aeaf046d49025fd109f67ef37320b8223836a23b554583")
        self.assertEqual(set(json.loads(implicit[1][1]["content"])), {"documents", "questions", "public_protocol"})
        self.assertEqual(implicit[2], {"model": "fixture", "temperature": 0.0, "max_tokens": 16384, "retries": 1, "strict_json": True})

    def test_collection_receives_validated_observations_not_messages_or_raw_audit_logs(self):
        report = self.run_report()
        evidence = prepare_collection_evidence(self.snap, report)
        result = self.collection(evidence)
        self.assertTrue(result["proposal_ready"])
        self.assertEqual(result["collection_review_version"], MEASURED_VERSION)
        payload = json.loads(self.calls[-1][1][1]["content"])
        observation = payload["context_challenge_evidence"]
        self.assertEqual(observation, evidence["observation"])
        self.assertNotIn("source_report", observation)
        self.assertNotIn("messages", observation["trials"][0])
        self.assertEqual(observation["trials"][0]["raw_output"], self.raw)
        self.assertIn("不能", self.calls[-1][1][0]["content"])
        before = len(self.calls); bad = deepcopy(evidence); bad["observation"]["calls_used"] = 99
        with self.assertRaises(ValueError): self.collection(bad)
        self.assertEqual(len(self.calls), before)


if __name__ == "__main__": unittest.main(verbosity=2)
