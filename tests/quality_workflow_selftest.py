"""Offline version, isolation and receipt regressions; not model accuracy tests.

All reader, critic and grader outputs are explicit scripted proposals. No provider
configuration or model API is imported or called.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from threading import Event, Lock
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.quality_workflow import (snapshot, change_impact, fresh_review,
    validate_snapshot, validate_review, make_receipt, grade_system_answers, FreshReviewAuditError)
from pipeline.semantic_review import VERSION as REVIEW_VERSION, visible_documents


CORPUS = {"world": "PRIVATE_WORLD", "sessions": [{
    "session_id": 1, "date": "2025-01-09", "docs": [
        {"doc_id": "PRIVATE_SOURCE", "title": "PRIVATE_TITLE",
         "content": "报告客户是北溟保险，所属市场为欧洲。", "writer_notes": "PRIVATE_WRITER"},
        {"content": "登记不等于分析确认。", "is_filler": True}]}]}
PROTOCOL = "仅根据所有公开正文回答；信息不足时说明限制。"
QUESTIONS = [
    {"qid": "q-client", "question": "报告的客户是谁？",
     "reference_proposal": {"answer": "北溟保险", "rationale": "正文明确给出客户。"},
     "author_issue": "PRIVATE_CLIENT_ISSUE"},
    {"qid": "q-market", "question": "报告客户所属市场在哪里？",
     "reference_proposal": {"answer": "欧洲", "rationale": "正文明确给出市场。"},
     "author_issue": "PRIVATE_MARKET_ISSUE"}]


def review_output(question, stage):
    """Known fixture values, deliberately not a semantic solver."""
    q = next(q for q in QUESTIONS if q["question"] == question)
    answer = q["reference_proposal"]["answer"]
    result = {"interpretation": question, "major_requirements": [question],
        "answerability": "answerable", "reasoning": "测试预设：正文支持这个回答。",
        "coverage": {"status": "complete", "scope_conflict": False, "limitations": []},
        "evidence": [{"doc_id": "d000001", "field": "content", "quote": answer,
                      "role": "support", "explanation": "测试预设的公开直接依据。"}]}
    if stage == "blind_read":
        result["answer"] = answer
    else:
        result.update(item_validity="valid", reference_status="supported", reviewed_answer=None,
            reviewed_rationale=None, concerns=[],
            original_answer_review=[{"requirement": question, "assessment": "已回答",
                                     "explanation": "测试预设：原答案覆盖主要任务。"}],
            original_rationale_review={"status": "reviewed", "claims": [{
                "claim": "正文给出所问事实", "assessment": "成立", "explanation": "公开正文支持。"}],
                "limitations": []},
            review_findings={"substantive_defects": [], "acceptable_brevity": [],
                             "editorial_suggestions": []})
    return result


class ReaderScript:
    def __init__(self, changes=None):
        self.changes = changes or {}
        self.calls = []
        self.lock = Lock()

    def __call__(self, step, messages, **params):
        stage = step.rsplit(".", 1)[-1]
        payload = json.loads(messages[1]["content"])
        with self.lock:
            self.calls.append({"stage": stage, "messages": deepcopy(messages),
                               "payload": payload, "params": deepcopy(params)})
        change = self.changes.get((payload["question"], stage), {})
        if isinstance(change, Exception):
            raise change
        return {**review_output(payload["question"], stage), **deepcopy(change)}


def run_review(frozen, script=None, **kwargs):
    options = {"reader_model": "offline-reader", "reviewer_model": "offline-critic",
               "max_calls": 2 * len(frozen["questions"]), "max_input_chars": 200000,
               "max_tokens": 4096, "workers": 2}
    options.update(kwargs)
    return fresh_review(frozen, chat_json=script or ReaderScript(), **options)


def predictions(frozen):
    return [{"system": system, "qid": qid, "pred": answer,
             "prediction_input_hash": frozen["identities"][qid]["prediction_input_hash"]}
        for system, qid, answer in (("system-a", "q-client", "北溟保险"),
                                   ("system-a", "q-market", "欧洲"),
                                   ("system-b", "q-client", "北溟保险。"),
                                   ("system-c", "q-client", "第三个回答提出原参考的问题。"))
        if qid in frozen["identities"]]


class FreshReviewTests(unittest.TestCase):
    def test_each_question_has_a_fresh_independent_full_public_context(self):
        frozen = snapshot(QUESTIONS, CORPUS, PROTOCOL)
        frozen["author_issues"] = "PRIVATE_UNRESOLVED_ISSUES"
        script, callback_parts = ReaderScript(), []
        original = deepcopy(frozen)
        report = run_review(frozen, script, on_item=lambda index, part: callback_parts.append((index, part)))
        self.assertEqual(frozen, original)
        self.assertEqual(report["calls_used"], 4)
        self.assertEqual(report["version"], REVIEW_VERSION)
        self.assertEqual(report["snapshot_id"], frozen["snapshot_id"])
        self.assertEqual(len(callback_parts), 2)
        public_docs, _ = visible_documents(CORPUS)
        for call in script.calls:
            payload = call["payload"]
            own = next(q for q in QUESTIONS if q["question"] == payload["question"])
            other = next(q for q in QUESTIONS if q is not own)
            self.assertEqual(payload["documents"], public_docs)
            self.assertEqual(payload["public_protocol"], PROTOCOL)
            self.assertNotIn(other["question"], json.dumps(payload, ensure_ascii=False))
            self.assertNotIn("PRIVATE", json.dumps(payload, ensure_ascii=False))
            self.assertEqual(len(call["messages"]), 2)
            self.assertEqual(call["params"]["retries"], 1)
            if call["stage"] == "blind_read":
                self.assertEqual(set(payload), {"question", "public_protocol", "documents", "input_scope"})
            else:
                self.assertEqual(payload["reference_proposal"], own["reference_proposal"])
                self.assertEqual(payload["blind_read"]["interpretation"], own["question"])
        self.assertEqual([item["source_qid"] for item in report["items"]], [q["qid"] for q in QUESTIONS])
        self.assertEqual(len({item["candidate_id"] for item in report["items"]}), 2)
        self.assertEqual(set(validate_review(frozen, report)), set(frozen["identities"]))
        self.assertEqual(len(report["records"]), 8)
        for event in report["records"]:
            item = next(i for i in report["items"] if i["source_qid"] == event["source_qid"])
            self.assertEqual(event["candidate_id"], item["candidate_id"])

    def test_zero_odd_and_excess_budgets_are_bounded_without_dropping_candidates(self):
        frozen = snapshot(QUESTIONS, CORPUS, PROTOCOL)
        for budget, used, allocation, completed in ((0, 0, [0, 0], 0), (1, 1, [1, 0], 0),
                (3, 3, [2, 1], 1), (4, 4, [2, 2], 2), (100, 4, [2, 2], 2)):
            with self.subTest(budget=budget):
                script = ReaderScript()
                report = run_review(frozen, script, max_calls=budget)
                self.assertEqual(len(report["items"]), 2)
                self.assertEqual(report["calls_used"], used)
                self.assertEqual(len(script.calls), used)
                self.assertEqual(report["budget"]["per_candidate_calls"], allocation)
                self.assertEqual(sum(i["review_state"] == "completed" for i in report["items"]), completed)

    def test_invalid_budget_or_workers_rejected_before_model(self):
        frozen = snapshot(QUESTIONS, CORPUS, PROTOCOL)
        for kwargs in ({"max_calls": -1}, {"max_calls": True}, {"workers": 0},
                       {"workers": 5}, {"workers": True}):
            script = ReaderScript()
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                run_review(frozen, script, **kwargs)
            self.assertEqual(script.calls, [])

    def test_input_cap_is_not_silent_truncation(self):
        frozen = snapshot(QUESTIONS, CORPUS, PROTOCOL)
        script = ReaderScript()
        report = run_review(frozen, script, max_input_chars=1)
        self.assertEqual(script.calls, [])
        self.assertEqual(report["calls_used"], 0)
        self.assertEqual([i["execution"]["status"] for i in report["items"]], ["input_limit"] * 2)
        self.assertEqual(report["documents"], visible_documents(CORPUS)[0])


class FreshReviewAuditTests(unittest.TestCase):
    def setUp(self):
        self.frozen = snapshot(QUESTIONS, CORPUS, PROTOCOL)

    def test_isolated_sink_failure_stops_queued_item_and_preserves_both_identities(self):
        from isolated_workflow_selftest import IsolatedScript
        script, rejected = IsolatedScript(), []
        def record(event):
            if event["event"] == "finished" and event["stage"] == "reference_audit":
                rejected.append(event["source_qid"])
                raise OSError("one audit write failed")
        with self.assertRaises(FreshReviewAuditError) as caught:
            run_review(self.frozen, script, max_calls=6, workers=1,
                       reference_auditor_model="offline-auditor", record=record)
        report = caught.exception.report
        self.assertEqual(len(script.calls), 2)
        self.assertEqual(report["calls_used"], 2)
        self.assertEqual(rejected, ["q-client"])
        self.assertEqual([i["source_qid"] for i in report["items"]], ["q-client", "q-market"])
        self.assertEqual(report["items"][1]["execution"]["status"], "audit_stopped")
        self.assertEqual(report["items"][0]["reference_audit"]["raw_output"]["decision"], "accept")
        self.assertEqual(report["audit_failures"][0]["callback"], "record")
        self.assertEqual(report["provider_dispatches"][-1]["raw_output"]["decision"], "accept")
        self.assertEqual(make_receipt(self.frozen, self.frozen, report)["counts"]["unresolved"], 2)

    def test_v6_exception_without_child_report_retains_raw_events_and_queued_identity(self):
        script = ReaderScript()
        def record(event):
            if event["event"] == "finished":
                raise OSError("legacy sink failure")
        with self.assertRaises(FreshReviewAuditError) as caught:
            run_review(self.frozen, script, workers=1, record=record)
        report = caught.exception.report
        self.assertEqual(len(script.calls), 1)
        self.assertEqual(report["calls_used"], 1)
        self.assertEqual(len(report["items"]), 2)
        self.assertFalse(report["items"][0]["workflow_execution"]["partial_report_available"])
        returned = [e for e in report["records"] if e["event"] == "finished"]
        self.assertEqual(returned[0]["output"]["answer"], "北溟保险")
        self.assertEqual(returned[0]["source_qid"], "q-client")
        self.assertEqual(report["execution"]["status"], "audit_error")

    def test_on_item_failure_stops_next_item_without_relabelling_question_bad(self):
        script, checkpoints = ReaderScript(), []
        def checkpoint(index, part):
            checkpoints.append(index)
            raise OSError("checkpoint failed")
        with self.assertRaises(FreshReviewAuditError) as caught:
            run_review(self.frozen, script, workers=1, on_item=checkpoint)
        report = caught.exception.report
        self.assertEqual(checkpoints, [0])
        self.assertEqual(len(script.calls), 2)
        self.assertEqual(report["calls_used"], 2)
        self.assertTrue(report["items"][0]["workflow_execution"]["partial_report_available"])
        self.assertEqual(report["items"][0]["adjudication"]["item_validity"], "valid")
        self.assertEqual(report["items"][0]["review_state"], "pending")
        self.assertEqual(report["audit_failures"][0]["callback"], "on_item")
        self.assertEqual(make_receipt(self.frozen, self.frozen, report)["counts"]["unresolved"], 2)

    def test_parallel_inflight_return_is_retained_and_no_followup_call_is_dispatched(self):
        from isolated_workflow_selftest import IsolatedScript
        script, market_inflight, release_market = IsolatedScript(), Event(), Event()
        seen, guard = [], Lock()
        def provider(step, messages, **params):
            payload = json.loads(messages[1]["content"])
            with guard:
                seen.append((payload["question"], step))
            if step.endswith("blind_read"):
                if payload["question"] == QUESTIONS[1]["question"]:
                    market_inflight.set()
                    if not release_market.wait(5):
                        raise AssertionError("audit failure did not release in-flight fixture")
                elif not market_inflight.wait(5):
                    raise AssertionError("second worker did not dispatch")
            return script(step, messages, **params)
        def record(event):
            if (event["source_qid"] == "q-client" and event["stage"] == "reference_audit"
                    and event["event"] == "finished"):
                release_market.set()
                raise OSError("parallel sink failed")
        try:
            with self.assertRaises(FreshReviewAuditError) as caught:
                run_review(self.frozen, provider, max_calls=6, workers=2,
                           reference_auditor_model="offline-auditor", record=record)
        finally:
            release_market.set()
        report = caught.exception.report
        self.assertEqual(len(seen), 3)
        self.assertEqual(report["calls_used"], 3)
        self.assertEqual(len(script.calls), 3)
        self.assertTrue(all(e["status"] == "returned" for e in report["provider_dispatches"]))
        self.assertFalse(any(step.endswith("adjudicate") for _, step in seen))
        market = [e for e in report["records"]
                  if e["source_qid"] == "q-market" and e["event"] == "finished"]
        self.assertEqual(market[0]["output"]["answer"], "欧洲")
        self.assertEqual(len(report["items"]), 2)
        self.assertTrue(all(i["review_state"] == "pending" for i in report["items"]))

    def test_started_sink_failure_spends_no_provider_calls(self):
        script = ReaderScript()
        def record(event):
            raise OSError("sink failed before dispatch")
        with self.assertRaises(FreshReviewAuditError) as caught:
            run_review(self.frozen, script, workers=2, record=record)
        report = caught.exception.report
        self.assertEqual(script.calls, [])
        self.assertEqual(report["calls_used"], 0)
        self.assertEqual(report["provider_dispatches"], [])
        self.assertEqual(len(report["items"]), 2)


class BindingValidationTests(unittest.TestCase):
    def setUp(self):
        self.frozen = snapshot(QUESTIONS, CORPUS, PROTOCOL)
        self.report = run_review(self.frozen)

    def test_stale_corpus_protocol_question_and_reference_are_rejected(self):
        corpus = deepcopy(CORPUS)
        corpus["sessions"][0]["docs"][1]["content"] += "增加一份此前未引用的更正记录。"
        question = deepcopy(QUESTIONS)
        question[0]["question"] += "请解释。"
        reference = deepcopy(QUESTIONS)
        reference[0]["reference_proposal"]["rationale"] = "重新审查后的说明。"
        for changed in (snapshot(QUESTIONS, corpus, PROTOCOL), snapshot(QUESTIONS, CORPUS, PROTOCOL + "新增规则"),
                        snapshot(question, CORPUS, PROTOCOL), snapshot(reference, CORPUS, PROTOCOL)):
            with self.subTest(snapshot_id=changed["snapshot_id"]), self.assertRaises(ValueError):
                validate_review(changed, self.report)

    def test_bound_labels_do_not_hide_altered_inputs_or_candidate_identity(self):
        for target in ("documents", "public_protocol", "reference_proposal", "question", "source_qid"):
            report = deepcopy(self.report)
            if target == "documents":
                report["documents"][0]["content"] = "tampered"
            elif target == "public_protocol":
                report[target] = "tampered"
            else:
                report["items"][0][target] = "tampered"
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate_review(self.frozen, report)

    def test_completed_locator_and_semantic_mirror_tampering_are_rejected(self):
        for target in ("raw_quote", "stage_resolved", "final_resolved", "location_entry", "location_status",
                       "reference_status", "review_findings", "certification"):
            report = deepcopy(self.report)
            item = report["items"][0]
            if target == "raw_quote":
                item["blind_read"]["evidence"][0]["quote"] = "not in the document"
            elif target == "stage_resolved":
                item["stage_resolved_evidence"]["blind_read"][0]["resolved_text"] = "tampered"
            elif target == "final_resolved":
                item["resolved_evidence"][0]["field_hash"] = "tampered"
            elif target == "location_entry":
                item["stage_evidence_location"]["adjudicate"]["entries"][0]["resolved"]["resolved_text"] = "tampered"
            elif target == "location_status":
                item["evidence_location"]["status"] = "failed"
            elif target == "reference_status":
                item["reference_status"] = "contradicted"
            elif target == "review_findings":
                item["review_findings"]["substantive_defects"] = ["tampered"]
            else:
                item["item_certification"]["reasons"] = ["tampered"]
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate_review(self.frozen, report)

    def test_pending_location_failure_remains_readable_and_cannot_be_labelled_completed(self):
        bad = review_output(QUESTIONS[0]["question"], "blind_read")["evidence"]
        bad[0]["quote"] = "unlocatable"
        report = run_review(self.frozen, ReaderScript({(QUESTIONS[0]["question"], "blind_read"): {"evidence": bad}}))
        item = validate_review(self.frozen, report)["q-client"]
        self.assertEqual(item["review_state"], "pending")
        self.assertEqual(item["adjudication"]["reference_status"], "supported")
        self.assertEqual(report["calls_used"], 4)
        report["items"][0]["review_state"] = "completed"
        with self.assertRaises(ValueError):
            validate_review(self.frozen, report)

    def test_reference_presence_change_requires_review_and_regrade(self):
        absent = deepcopy(QUESTIONS[:1])
        absent[0].pop("reference_proposal")
        explicit_null = deepcopy(absent)
        explicit_null[0]["reference_proposal"] = None
        before = snapshot(absent, CORPUS, PROTOCOL)
        after = snapshot(explicit_null, CORPUS, PROTOCOL, parent=before)
        self.assertEqual(before["identities"]["q-client"]["prediction_input_hash"],
                         after["identities"]["q-client"]["prediction_input_hash"])
        self.assertEqual(change_impact(before, after)["q-client"], {
            "action": "reuse_prediction_regrade", "fresh_review_required": True, "reuse_old_grade": False})


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.frozen = snapshot(QUESTIONS[:1], CORPUS, PROTOCOL)

    def receipt(self, changes=None, **kwargs):
        script = ReaderScript({(QUESTIONS[0]["question"], "adjudicate"): changes or {}})
        report = run_review(self.frozen, script)
        return make_receipt(self.frozen, self.frozen, report, **kwargs)

    def test_main_reference_dispute_and_rationale_package_defect_are_distinct(self):
        cases = [({"reference_status": "unsupported"}, "Original reference proposal disputed or unresolved"),
                 ({"review_findings": {"substantive_defects": ["理由错误声称经过监管审批。"],
                    "acceptable_brevity": [], "editorial_suggestions": []}}, "Critic reports substantive package defects")]
        for change, expected in cases:
            with self.subTest(change=change):
                result = self.receipt(change)
                row = result["rows"][0]
                self.assertEqual(row["status"], "unresolved")
                self.assertIn(expected, row["reasons"])
                if "review_findings" in change:
                    self.assertNotIn("Original reference proposal disputed or unresolved", row["reasons"])
                self.assertEqual(result["counts"]["model_review_eligible"], 0)
                self.assertFalse(result["official_release"])

    def test_acceptable_brevity_and_editorial_suggestions_do_not_reject_item(self):
        result = self.receipt({"review_findings": {"substantive_defects": [],
            "acceptable_brevity": ["仅给名字已完整回答。"], "editorial_suggestions": ["可选补充一句出处。"]}})
        self.assertEqual(result["rows"][0]["status"], "model_review_eligible")
        self.assertEqual(result["rows"][0]["reasons"], [])
        self.assertEqual(result["counts"], {"model_review_eligible": 1, "unresolved": 0, "not_selected": 0})
        self.assertFalse(result["official_release"])
        self.assertEqual(result["publication_effect"], "none")

    def test_incomplete_review_is_unresolved_and_never_counted_as_bad(self):
        report = run_review(self.frozen, max_calls=0)
        result = make_receipt(self.frozen, self.frozen, report)
        self.assertEqual(result["counts"], {"model_review_eligible": 0, "unresolved": 1, "not_selected": 0})
        self.assertIn("Review incomplete; not evidence of a bad question", result["rows"][0]["reasons"])
        self.assertNotIn("bad", result["counts"])

    def test_removed_original_retains_disposition_and_new_item_cannot_count_as_repair(self):
        original = snapshot(QUESTIONS, CORPUS, PROTOCOL)
        current = snapshot(QUESTIONS[:1], CORPUS, PROTOCOL, parent=original)
        result = make_receipt(original, current, run_review(current),
                              dispositions={"q-market": "人工决定本轮不保留。"})
        self.assertEqual(result["original_count"], 2)
        self.assertEqual(result["counts"]["not_selected"], 1)
        self.assertEqual(result["rows"][1]["reasons"], ["人工决定本轮不保留。"])
        with self.assertRaisesRegex(ValueError, "new questions"):
            make_receipt(self.frozen, original, run_review(original))

    def test_editor_unresolved_disposition_is_distinct_from_explicit_drop(self):
        original = snapshot(QUESTIONS, CORPUS, PROTOCOL)
        current = snapshot(QUESTIONS[:1], CORPUS, PROTOCOL, parent=original)
        review = run_review(current)
        for action, status in (("unresolved", "unresolved"), ("drop", "not_selected")):
            with self.subTest(action=action):
                receipt = make_receipt(original, current, review, dispositions={
                    "q-market": {"action": action, "reason": "编辑者显式记录该处置。"}})
                row = next(row for row in receipt["rows"] if row["qid"] == "q-market")
                self.assertEqual(row["status"], status)
                self.assertEqual(row["reasons"], ["编辑者显式记录该处置。"])
                self.assertEqual(receipt["counts"][status], 1)
                self.assertEqual(receipt["original_count"], 2)


class SnapshotValidationTests(unittest.TestCase):
    def setUp(self):
        self.original = snapshot(QUESTIONS, CORPUS, PROTOCOL)
        changed = deepcopy(CORPUS)
        changed["sessions"][0]["docs"][0]["content"] += "新增一项公开事实。"
        self.current = snapshot(QUESTIONS, changed, PROTOCOL, parent=self.original)

    def test_mixed_old_identities_rejected_before_any_grader(self):
        review = run_review(self.current)
        mixed = deepcopy(self.current)
        mixed["identities"] = deepcopy(self.original["identities"])
        factory = Mock()
        with self.assertRaisesRegex(ValueError, "identities"):
            grade_system_answers(mixed, review, predictions(self.original), judge_factory=factory)
        factory.assert_not_called()

    def test_each_derived_field_is_recomputed(self):
        for field in ("snapshot_id", "identities", "public_context_hash", "version"):
            mixed = deepcopy(self.current)
            mixed[field] = "invalid" if field == "version" else deepcopy(self.original[field])
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_snapshot(mixed)

    def test_change_impact_and_review_reject_before_using_mixed_identity(self):
        mixed = deepcopy(self.current); mixed["identities"] = self.original["identities"]
        with self.assertRaises(ValueError): change_impact(self.original, mixed)
        fake = ReaderScript()
        with self.assertRaises(ValueError): run_review(mixed, fake)
        self.assertEqual(fake.calls, [])

    def test_parent_check_and_formatting_preserve_valid_values(self):
        current = json.loads(json.dumps(self.current, ensure_ascii=False, indent=5))
        current["audit_note"] = "allowed adjacent metadata"
        self.assertIs(validate_snapshot(current, parent=self.original), current)
        broken_parent = deepcopy(self.original); broken_parent["snapshot_id"] = "stale"
        with self.assertRaises(ValueError):
            snapshot(QUESTIONS, CORPUS, PROTOCOL, parent=broken_parent)
        with self.assertRaises(ValueError):
            validate_snapshot(current, parent=self.current)


class GradingCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.frozen = snapshot(QUESTIONS, CORPUS, PROTOCOL)
        self.review = run_review(self.frozen)

    def test_late_dispute_quarantines_previous_and_later_same_item_systems_only(self):
        inputs = predictions(self.frozen)
        disputed_answer = inputs[-1]["pred"]
        inputs.append({**inputs[0], "system": "system-d", "pred": "后续另一个正确回答。"})
        before = deepcopy(inputs)
        calls = []
        def judge(question, pred):
            calls.append((question["qid"], pred))
            dispute = pred == disputed_answer
            return {"verdict": "uncertain" if dispute else "correct", "correct": None if dispute else True,
                    "execution_status": "ok", "requires_item_reassessment": dispute,
                    "item_reassessment": {"required": dispute, "reasons": []},
                    "reason": "Late original-reference dispute" if dispute else "Scripted supported answer"}
        factory = Mock(return_value=judge)
        result = grade_system_answers(self.frozen, self.review, inputs, judge_factory=factory)
        self.assertEqual(len(calls), len(inputs))
        self.assertEqual(inputs, before)
        self.assertEqual(result["requires_item_reassessment"], ["q-client"])
        self.assertFalse(result["old_grades_reused"])
        for row in result["rows"]:
            if row["qid"] == "q-client":
                self.assertTrue(row["quarantined"])
                self.assertIsNone(row["correct"])
                self.assertEqual(row["judgement"]["execution_status"], "quarantined")
                self.assertTrue(row["judgement"]["item_reassessment"]["required"])
                self.assertEqual(row["judgement"]["item_reassessment"]["scope"], "same_item_all_systems")
                self.assertIn("original_judgement", row)
            else:
                self.assertFalse(row["quarantined"])
                self.assertTrue(row["correct"])
                self.assertNotIn("original_judgement", row)
        self.assertTrue(result["rows"][0]["original_judgement"]["correct"])
        receipt = make_receipt(self.frozen, self.frozen, self.review, material_ready=True,
                               calibration={"status": "passed"}, grades=result["rows"])
        self.assertFalse(receipt["scoring_ready"])
        self.assertEqual(receipt["quarantined_qids"], ["q-client"])

    def test_reference_only_revision_requires_fresh_review_but_reuses_predictions_for_new_grades(self):
        inputs = predictions(self.frozen)
        revised_q = deepcopy(QUESTIONS)
        revised_q[0]["reference_proposal"]["rationale"] = "正文直接指出客户；这不声称监管已审批。"
        revised = snapshot(revised_q, CORPUS, PROTOCOL, parent=self.frozen)
        impact = change_impact(self.frozen, revised)
        self.assertEqual(impact["q-client"], {"action": "reuse_prediction_regrade",
                                             "fresh_review_required": True, "reuse_old_grade": False})
        self.assertEqual(revised["identities"]["q-client"]["prediction_input_hash"],
                         self.frozen["identities"]["q-client"]["prediction_input_hash"])
        untouched = Mock()
        with self.assertRaises(ValueError):
            grade_system_answers(revised, self.review, inputs, judge_factory=untouched)
        untouched.assert_not_called()
        new_review = run_review(revised)
        grader = Mock(return_value={"correct": True, "verdict": "correct", "requires_item_reassessment": False})
        result = grade_system_answers(revised, new_review, inputs, judge_factory=Mock(return_value=grader))
        self.assertEqual(grader.call_count, len(inputs))
        self.assertTrue(all(row["correct"] for row in result["rows"]))
        self.assertFalse(result["old_grades_reused"])
        self.assertTrue(all(row["snapshot_id"] == revised["snapshot_id"] for row in result["rows"]))

    def test_uncited_corpus_change_requires_new_predictions_even_after_fresh_review(self):
        corpus = deepcopy(CORPUS)
        corpus["sessions"][0]["docs"][1]["content"] += "档案边界已改变。"
        revised = snapshot(QUESTIONS, corpus, PROTOCOL, parent=self.frozen)
        self.assertTrue(all(row["action"] == "reanswer_and_regrade"
                            for row in change_impact(self.frozen, revised).values()))
        fresh = run_review(revised)
        factory = Mock()
        with self.assertRaisesRegex(ValueError, "re-answering"):
            grade_system_answers(revised, fresh, predictions(self.frozen), judge_factory=factory)
        factory.assert_not_called()
        grade = Mock(return_value={"correct": True, "requires_item_reassessment": False})
        result = grade_system_answers(revised, fresh, predictions(revised), judge_factory=Mock(return_value=grade))
        self.assertEqual(grade.call_count, len(predictions(revised)))
        self.assertTrue(all(not row["quarantined"] for row in result["rows"]))

    def test_all_predictions_are_prevalidated_before_any_grade_call(self):
        for fault in ("duplicate", "unknown_qid", "missing_pred", "wrong_hash"):
            rows = predictions(self.frozen)
            if fault == "duplicate":
                rows.append(deepcopy(rows[0]))
            elif fault == "unknown_qid":
                rows[-1]["qid"] = "missing"
            elif fault == "missing_pred":
                rows[-1].pop("pred")
            else:
                rows[-1]["prediction_input_hash"] = "stale"
            factory = Mock()
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                grade_system_answers(self.frozen, self.review, rows, judge_factory=factory)
            factory.assert_not_called()

    def test_real_grade_fixture_has_bound_question_reference_and_is_scored(self):
        from eval.semantic_judge import SemanticJudge
        from eval.grading import is_scored
        output = review_output(QUESTIONS[0]["question"], "adjudicate")
        output.update(answer_verdict="correct", format_compliance="compliant",
            additional_facts={"status": "not_assessed", "reason": "没有附加要求。"},
            answer_review={"primary_task": "回答客户是谁。", "answer_meaning": "客户为北溟保险。",
                "claims": [{"claim": "客户为北溟保险", "task_role": "primary", "assessment": "supported",
                            "evidence_indices": [0], "explanation": "正文直接给出客户名。"}],
                "reference_comparison": "与原参考和完整公开材料一致。"},
            requires_item_reassessment=False, reassessment_reason="未发现需重审的问题。")
        call = Mock(return_value=output)
        def factory(frozen, review):
            return SemanticJudge(review, frozen["questions"], frozen["corpus"], frozen["protocol"],
                                 model="offline-grader", chat_json=call, max_calls=1)
        row = predictions(self.frozen)[0]
        row.update(question="预测文件不应覆盖实际题目", reference_proposal={"answer": "伪造参考"})
        result = grade_system_answers(self.frozen, self.review, [row], judge_factory=factory)
        graded = result["rows"][0]
        call.assert_called_once()
        self.assertEqual(graded["question"], self.frozen["questions"][0]["question"])
        self.assertEqual(graded["reference_proposal"], self.frozen["questions"][0]["reference_proposal"])
        self.assertTrue(graded["correct"])
        self.assertTrue(is_scored(graded))
        self.assertFalse(is_scored({**graded, "question": "评分后的篡改"}))
        self.assertFalse(is_scored({**graded, "reference_proposal": {"answer": "评分后的篡改"}}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
