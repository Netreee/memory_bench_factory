"""Offline integration: scripted agent opinions are fixtures, never accuracy evidence."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from threading import Event, Lock
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.agent_factory import run_agent_case, AgentCaseError, PHASES, REVIEW_PHASES, TEAM_PHASES
from pipeline.quality_workflow import validate_review, change_impact, snapshot, grade_system_answers


PROTOCOL = "只依据公开资料回答；不能确定时说明。"
BEFORE = "客户是北溟保险。地区是欧洲。会费合计20+30=40。"
AFTER = "客户是北溟保险。地区是欧洲。会费合计20+30=50。"
QUESTIONS = ["客户是谁？", "客户属于哪个地区？"]


def plan(cap=14):
    phases = {}
    for name in PHASES:
        item = {"max_calls": 4 if name in REVIEW_PHASES else 1,
                "max_input_chars": 200000, "max_tokens": 4096}
        if name in REVIEW_PHASES:
            item.update(reader_model=name + ".reader", reviewer_model=name + ".critic", workers=2)
        else:
            item["model"] = name
        phases[name] = item
    return {"max_provider_attempts": cap,
        "material_batches": [{"session_id": 0, "date": "2025-01-01", "intent": "业务收支", "document_count": 1, "target_chars": 10}],
        "question_batches": [{"focus": "客户与地区", "count": 2}], "phases": phases}


def team_plan(cap=19):
    from pipeline.material_team import PHASES as roles
    value = plan(cap)
    value["material_mode"] = "team/v1"
    for name in ("material_review", "material_edit", "material_rereview"):
        value["phases"].pop(name)
    value["phases"]["material_team"] = {"max_calls": 6, "max_input_chars": 200000,
        "max_tokens": 4096, "models": {role: "team." + role for role in roles}}
    value["phases"]["collection_review"] = {"max_calls": 1, "max_input_chars": 200000,
        "max_tokens": 4096, "model": "collection_review"}
    value["phases"]["final_collection_review"] = {"max_calls": 1, "max_input_chars": 200000,
        "max_tokens": 4096, "model": "final_collection_review"}
    return value


def evidence():
    return [{"doc_id": "d000001", "field": "content", "location_scope": "field",
             "role": "support", "explanation": "测试预设的直接正文。"}]


class Script:
    def __init__(self, *, material_mode="revise", bad_qid=False, drop_second=False,
                 bad_generation=False, material_rereview_error=False, callback_mutation=False):
        self.calls, self.lock = [], Lock()
        self.material_mode, self.bad_qid, self.drop_second = material_mode, bad_qid, drop_second
        self.bad_generation, self.material_rereview_error = bad_generation, material_rereview_error

    def __call__(self, step, messages, **params):
        payload = json.loads(messages[1]["content"])
        phase = params["model"].split(".")[0]
        with self.lock:
            self.calls.append({"phase": phase, "step": step, "payload": deepcopy(payload),
                               "messages": deepcopy(messages), "params": deepcopy(params)})
        if phase == "material_generation":
            return {"corpus": {"sessions": [{"session_id": 0, "date": "WRONG" if self.bad_generation else "2025-01-01",
                "docs": [{"title": "PRIVATE_TITLE", "content": BEFORE}]}]}, "public_protocol": "PRIVATE_PROPOSED_PROTOCOL",
                "design_notes": {"private": "PRIVATE_AUTHOR_NOTES"}, "limitations": []}
        if phase in {"material_review", "material_rereview"}:
            if phase == "material_rereview" and self.material_rereview_error:
                raise RuntimeError("scripted reader failure")
            return {"assessment": "测试意见：初版算术需核对。" if phase == "material_review" else "测试意见：正文可以理解。",
                "issues": [{"issue_id": "m1", "description": "20+30不是40。", "evidence": evidence()}] if phase == "material_review" else [],
                "role_differences": [], "business_use_observations": ["会费记录有业务用途。"], "limitations": []}
        if phase == "material_edit":
            responses = [{"issue_id": "m1", "disposition": "addressed", "reason": "按正文数值更正合计。"}]
            if self.material_mode == "no_change":
                return {"action": "no_change", "reason": "测试编辑选择不改。", "document_edits": [], "issue_responses": responses}
            if self.material_mode == "unresolved":
                return {"action": "unresolved", "reason": "测试编辑未能确定。", "document_edits": [], "issue_responses": responses}
            edit = {"doc_id": "d000001", "content": AFTER}
            if self.material_mode == "metadata":
                edit["date"] = "2025-01-02"
            return {"action": "revise", "reason": "最小正文更正。", "document_edits": [edit], "issue_responses": responses}
        if phase == "question_generation":
            return {"questions": [{"question": q,
                "reference_proposal": {"answer": "另一公司" if i == 0 else "欧洲", "rationale": "正文。"},
                "mechanism_target": {"intent": "PRIVATE_QUESTION_INTENT", "realization": "直接读取", "uncertainties": []},
                "evidence": evidence()} for i, q in enumerate(QUESTIONS)], "limitations": []}
        if phase == "question_edit":
            decisions = []
            for i, q in enumerate(payload["questions"]):
                action = "revise" if i == 0 else "drop" if self.drop_second else "keep"
                d = {"qid": "invented" if self.bad_qid and i == 1 else q["qid"], "action": action, "reason": "测试最小修改/处置。"}
                if action == "revise":
                    d["reference_proposal"] = {"answer": "北溟保险", "rationale": "正文直接列明客户。"}
                decisions.append(d)
            return {"decisions": decisions, "issue_responses": [{"issue_id": i["issue_id"],
                "disposition": "addressed", "reason": "核对了完整意见，包括合理省略。"} for i in payload["issues"]]}
        if phase in REVIEW_PHASES:
            answer = "北溟保险" if payload["question"] == QUESTIONS[0] else "欧洲"
            result = {"interpretation": payload["question"], "major_requirements": [payload["question"]],
                "answerability": "answerable", "coverage": {"status": "complete", "scope_conflict": False, "limitations": []},
                "evidence": evidence(), "reasoning": "DEVELOPMENT_READER_NOTE：测试预设依据。"}
            if step.endswith("blind_read"):
                result["answer"] = answer
            else:
                supported = payload["reference_proposal"]["answer"] == answer
                result.update(item_validity="valid", reference_status="supported" if supported else "unsupported",
                    reviewed_answer=None, reviewed_rationale=None, concerns=[] if supported else ["原参考客户不符。"],
                    original_answer_review=[{"requirement": payload["question"], "assessment": "已核对", "explanation": "完整核对题意。"}],
                    original_rationale_review={"status": "reviewed", "claims": [{"claim": "正文给出信息", "assessment": "支持", "explanation": "测试预设。"}], "limitations": []},
                    review_findings={"substantive_defects": [] if supported else ["参考客户错了。"],
                        "acceptable_brevity": ["无需强制补充其他数字。"], "editorial_suggestions": []})
            return result
        raise AssertionError(phase)


class TeamScript(Script):
    def __init__(self, *, team_action="revise", final_action="accept", invalid_collection=False, **kwargs):
        super().__init__(**kwargs)
        self.team_action, self.final_action = team_action, final_action
        self.invalid_collection = invalid_collection

    def __call__(self, step, messages, **params):
        model = params["model"]
        if not model.startswith("team.") and model not in {"collection_review", "final_collection_review"}:
            return super().__call__(step, messages, **params)
        payload = json.loads(messages[1]["content"])
        role = model.removeprefix("team.")
        self.calls.append({"phase": "material_team." + role if model.startswith("team.") else model,
            "step": step, "payload": deepcopy(payload), "messages": deepcopy(messages), "params": deepcopy(params)})
        if model in {"collection_review", "final_collection_review"}:
            ids = [q["qid"] for q in payload["questions"]]
            if self.invalid_collection and len(ids) > 1:
                ids[1] = "unknown"
            return {"assessment": "COLLECTION_PRIVATE_OPINION: coverage only.", "issues": [],
                "overlap_groups": [{"qids": ids, "assessment": "OVERLAP_FULL_OPINION", "suggested_response": "作者可反驳。"}] if len(ids) > 1 else [],
                "coverage_observations": ["COVERAGE_FULL_OPINION"], "limitations": ["难度未实测。"], "evidence": []}
        if role in {"continuity_reader", "use_reader", "fresh_reader"}:
            return {"assessment": "TEAM_READER_PRIVATE_OPINION", "findings": [],
                "preserved_uncertainties": ["保留未决。"], "limitations": [], "evidence": evidence()}
        if role == "triage":
            return {"action": self.team_action, "reason": "测试裁定。",
                "issues": [{"issue_id": "m1", "description": "20+30不是40。", "evidence": evidence()}]
                          if self.team_action == "revise" else [],
                "opinion_review": [], "preserved_uncertainties": [], "limitations": [], "evidence": evidence()}
        if role == "edit":
            return {"action": "revise", "reason": "仅修正文算术。",
                "document_edits": [{"doc_id": "d000001", "content": AFTER}],
                "issue_responses": [{"issue_id": "m1", "disposition": "addressed", "reason": "修正合计。"}]}
        if role == "decision":
            return {"action": self.final_action, "reason": "测试语义接受或保留。", "change_review": [],
                "opinion_review": [], "unresolved_findings": [], "preserved_uncertainties": [],
                "limitations": [], "evidence": evidence(), "original_evidence": []}
        raise AssertionError(role)


def run(script=None, config=None, **kwargs):
    script = script or Script()
    return run_agent_case({"private": "PRIVATE_SEED"}, {"intent": "PRIVATE_DESIGN"},
        {"intent": "PRIVATE_QUESTION_DESIGN"}, PROTOCOL, plan=config or plan(), chat_json=script, **kwargs), script


class FactoryIntegrationTests(unittest.TestCase):
    def test_complete_loop_keeps_versions_and_reviews_final_reference(self):
        report, script = run()
        self.assertEqual(report["execution"]["status"], "completed")
        self.assertEqual(report["budget"]["actual_provider_attempts"], 14)
        self.assertEqual(len(script.calls), 14)
        self.assertEqual(report["original_corpus"]["sessions"][0]["docs"][0]["content"], BEFORE)
        self.assertEqual(report["current_corpus"]["sessions"][0]["docs"][0]["content"], AFTER)
        self.assertEqual(report["current_corpus"]["sessions"][0]["date"], "2025-01-01")
        original, current = report["original_snapshot"], report["current_snapshot"]
        self.assertEqual(original["questions"][0]["reference_proposal"]["answer"], "另一公司")
        self.assertEqual(current["questions"][0]["reference_proposal"]["answer"], "北溟保险")
        self.assertEqual(current["parent_snapshot_id"], original["snapshot_id"])
        self.assertEqual(report["receipt"]["counts"]["model_review_eligible"], 2)
        self.assertFalse(report["receipt"]["material_ready"])
        self.assertFalse(report["receipt"]["scoring_ready"])
        self.assertFalse(report["receipt"]["official_release"])
        self.assertEqual(report["receipt"]["new_questions_from_revision"], 0)
        self.assertEqual(report["material_acceptance"]["review_phase"], "material_rereview")
        validate_review(current, report["phases"]["final_review"])
        with self.assertRaises(ValueError):
            validate_review(current, report["phases"]["question_review"])

    def test_no_private_context_or_previous_review_enters_fresh_readers(self):
        report, script = run()
        for call in script.calls:
            phase, payload = call["phase"], call["payload"]
            self.assertEqual(call["params"]["retries"], 1)
            if phase in {"material_review", "material_rereview"}:
                self.assertEqual(set(payload), {"documents", "public_protocol"})
            if phase in {"material_review", "material_rereview"} or call["step"].endswith("blind_read"):
                wire = json.dumps(payload)
                self.assertNotIn("PRIVATE", wire)
                self.assertNotIn("DEVELOPMENT_READER_NOTE", wire)
                self.assertNotIn("reference_proposal", payload)
                self.assertEqual(payload["public_protocol"], PROTOCOL)
            if phase in {"question_review", "final_review", "question_generation"}:
                self.assertEqual(payload["documents"][0]["content"], AFTER)
        editor = next(c for c in script.calls if c["phase"] == "question_edit")
        self.assertEqual(len(editor["payload"]["issues"]), 2)
        self.assertIn("acceptable_brevity", editor["payload"]["issues"][1]["description"])
        self.assertIn("DEVELOPMENT_READER_NOTE", editor["payload"]["issues"][1]["description"])

    def test_material_changes_invalidate_all_old_predictions_reference_only_regrades(self):
        report, _ = run()
        self.assertEqual({v["action"] for v in report["material_version_impact"]["by_qid"].values()}, {"reanswer_and_regrade"})
        self.assertEqual(report["question_version_impact"]["s001p000001"]["action"], "reuse_prediction_regrade")
        self.assertEqual(report["question_version_impact"]["s001p000002"]["action"], "unchanged")
        current = report["current_snapshot"]
        old = report["material_version_impact"]["before_snapshot"]
        prediction = {"system": "old", "qid": "s001p000001", "pred": "北溟保险",
                      "prediction_input_hash": old["identities"]["s001p000001"]["prediction_input_hash"]}
        with self.assertRaisesRegex(ValueError, "re-answering"):
            grade_system_answers(current, report["phases"]["final_review"], [prediction],
                judge_factory=lambda *_: self.fail("stale prediction reached grading"))

    def test_zero_global_budget_keeps_partial_material_and_skips_every_downstream_phase(self):
        report, script = run(config=plan(0))
        self.assertEqual(script.calls, [])
        self.assertEqual(report["budget"]["actual_provider_attempts"], 0)
        self.assertEqual(report["execution"]["reason"], "material_transport_incomplete")
        self.assertEqual(set(report["phases"]), set(PHASES))
        self.assertEqual(report["phases"]["material_generation"]["partial_corpus"], {"sessions": []})
        self.assertIsNone(report["original_snapshot"])

    def test_global_budget_stops_parallel_review_and_retains_every_original_qid(self):
        report, script = run(config=plan(8))
        self.assertEqual(len(script.calls), 8)
        self.assertEqual(report["budget"]["actual_provider_attempts"], 8)
        self.assertEqual(report["budget"]["phase_provider_attempts"]["question_review"], 3)
        self.assertEqual(report["budget"]["phase_provider_attempts"]["final_review"], 0)
        self.assertEqual(set(report["dispositions"]), {"s001p000001", "s001p000002"})
        self.assertEqual(len(report["phases"]["final_review"]["items"]), 2)
        self.assertEqual(report["receipt"]["counts"]["unresolved"], 2)
        self.assertEqual(report["execution"]["status"], "completed_with_execution_gaps")

    def test_zero_phase_budget_does_not_borrow_calls_from_another_phase(self):
        config = plan()
        config["phases"]["question_review"]["max_calls"] = 0
        report, script = run(config=config)
        self.assertEqual(report["budget"]["phase_provider_attempts"]["question_review"], 0)
        self.assertEqual(report["budget"]["phase_provider_attempts"]["final_review"], 4)
        self.assertEqual(len(script.calls), 10)
        self.assertEqual(len(report["phases"]["question_review"]["items"]), 2)
        self.assertEqual(report["receipt"]["original_count"], 2)

    def test_every_question_can_be_explicitly_dropped_without_refill(self):
        class DropAll(Script):
            def __call__(self, step, messages, **params):
                value = super().__call__(step, messages, **params)
                if params["model"] == "question_edit":
                    for decision in value["decisions"]:
                        decision["action"] = "drop"
                        decision.pop("reference_proposal", None)
                return value
        report, script = run(DropAll())
        self.assertEqual(report["current_snapshot"]["questions"], [])
        self.assertEqual(report["receipt"]["original_count"], 2)
        self.assertEqual(report["receipt"]["counts"]["not_selected"], 2)
        self.assertEqual(report["budget"]["phase_provider_attempts"]["final_review"], 0)
        self.assertFalse(report["receipt"]["scoring_ready"])

    def test_invalid_reader_payload_is_retained_for_editor_as_unvalidated(self):
        class MalformedInitialReader(Script):
            def __call__(self, step, messages, **params):
                value = super().__call__(step, messages, **params)
                if params["model"] == "question_review.reader":
                    return {"discussion": "RAW_READABLE_BUT_INVALID"}
                return value
        report, script = run(MalformedInitialReader())
        editor = next(c for c in script.calls if c["phase"] == "question_edit")
        for issue in editor["payload"]["issues"]:
            self.assertIn("RAW_READABLE_BUT_INVALID", issue["description"])
            self.assertIn("unvalidated_stage_returns", issue["description"])
        self.assertEqual(report["execution"]["status"], "completed_with_execution_gaps")
        self.assertEqual(report["receipt"]["counts"]["model_review_eligible"], 2)

    def test_metadata_edit_is_rejected_without_changing_corpus_or_using_old_approval(self):
        report, _ = run(Script(material_mode="metadata"))
        edit = report["phases"]["material_edit"]
        self.assertEqual(edit["execution"]["status"], "invalid_output")
        self.assertEqual(edit["raw_output"]["document_edits"][0]["date"], "2025-01-02")
        self.assertEqual(report["current_corpus"], report["original_corpus"])
        self.assertFalse(report["material_ready"])

    def test_transport_invalid_material_never_reaches_question_generation(self):
        report, script = run(Script(bad_generation=True))
        self.assertEqual(len(script.calls), 1)
        self.assertEqual(report["execution"]["reason"], "material_transport_incomplete")
        self.assertIsNone(report["current_corpus"])

    def test_failed_fresh_material_provider_stops_with_available_revision(self):
        script = Script(material_rereview_error=True)
        with self.assertRaises(AgentCaseError) as caught:
            run(script)
        report = caught.exception.report
        revised = report["phases"]["material_edit"]["proposed_corpus"]
        self.assertEqual(revised["sessions"][0]["docs"][0]["content"], AFTER)
        self.assertEqual(report["phases"]["material_rereview"]["execution"]["status"], "model_error")
        self.assertEqual(report["execution"], {"status": "transport_error", "phase": "material_rereview"})
        self.assertEqual(len(script.calls), 4)
        self.assertEqual(report["budget"]["actual_provider_attempts"], 4)
        self.assertNotIn("question_generation", report["phases"])
        self.assertIsNone(report["receipt"])
        self.assertFalse(report["material_ready"])

    def test_semantic_unresolved_material_does_not_become_execution_error_or_acceptance(self):
        report, _ = run(Script(material_mode="unresolved"))
        self.assertEqual(report["phases"]["material_edit"]["proposal_state"], "unresolved")
        self.assertEqual(report["execution"]["status"], "completed")
        self.assertEqual(report["material_acceptance"]["status"], "pending_attributed_decision")
        self.assertFalse(report["receipt"]["material_ready"])

    def test_editor_identity_failure_retains_all_originals_and_full_raw_proposal(self):
        report, _ = run(Script(bad_qid=True))
        self.assertEqual(report["phases"]["question_edit"]["execution"]["status"], "invalid_output")
        self.assertEqual(report["current_snapshot"]["questions"], report["original_snapshot"]["questions"])
        self.assertTrue(all(d["action"] == "retained_original_editor_incomplete" for d in report["dispositions"].values()))
        self.assertEqual(report["phases"]["question_edit"]["raw_output"]["decisions"][1]["qid"], "invented")

    def test_explicit_drop_keeps_original_denominator_and_reason(self):
        report, _ = run(Script(drop_second=True))
        self.assertEqual(report["receipt"]["original_count"], 2)
        self.assertEqual(len(report["current_snapshot"]["questions"]), 1)
        self.assertEqual(report["dispositions"]["s001p000002"]["action"], "drop")
        self.assertEqual(report["receipt"]["counts"]["not_selected"], 1)

    def test_callbacks_cannot_mutate_plan_or_future_inputs(self):
        config, records, phases = plan(), [], []
        unchanged = deepcopy(config)
        def sink(event):
            records.append(event)
            event.clear()
        def observer(event):
            phases.append(event["phase"])
            event["case_report"]["plan"]["max_provider_attempts"] = 0
            event["phase_report"].clear()
        report, _ = run(config=config, record=sink, on_phase=observer)
        self.assertEqual(config, unchanged)
        self.assertEqual(report["budget"]["actual_provider_attempts"], 14)
        self.assertEqual(phases, list(PHASES))
        self.assertTrue(report["records"])

    def test_audit_failure_after_response_retains_raw_and_stops_future_calls(self):
        script = Script()
        def fail(event):
            if event.get("event") == "finished":
                raise OSError("disk full")
        with self.assertRaises(AgentCaseError) as caught:
            run(script, record=fail)
        r = caught.exception.report
        self.assertEqual(len(script.calls), 1)
        self.assertEqual(r["budget"]["actual_provider_attempts"], 1)
        self.assertEqual(r["provider_attempts"][0]["raw_output"]["corpus"]["sessions"][0]["docs"][0]["content"], BEFORE)
        self.assertEqual(r["execution"]["status"], "audit_error")

    def test_failed_started_record_prevents_first_provider_attempt(self):
        script = Script()
        def fail(event):
            if event.get("event") == "started":
                raise OSError("cannot persist request")
        with self.assertRaises(AgentCaseError) as caught:
            run(script, record=fail)
        self.assertEqual(script.calls, [])
        self.assertEqual(caught.exception.report["budget"]["actual_provider_attempts"], 0)
        self.assertTrue(caught.exception.report["records"])

    def test_invalid_later_phase_plan_rejected_before_any_provider_attempt(self):
        config, script = plan(), Script()
        config["phases"]["final_review"]["workers"] = 0
        with self.assertRaises(ValueError):
            run(script, config)
        self.assertEqual(script.calls, [])

    def test_true_input_limit_does_not_truncate_or_issue_provider_call(self):
        config = plan()
        config["phases"]["material_generation"]["max_input_chars"] = 1
        report, script = run(config=config)
        self.assertEqual(script.calls, [])
        self.assertEqual(report["execution"]["reason"], "material_transport_incomplete")
        batch = report["phases"]["material_generation"]["batch_reports"][0]
        self.assertEqual(batch["execution"]["status"], "input_limit")
        self.assertIn("PRIVATE_SEED", batch["messages"][1]["content"])

    def test_team_mode_acceptance_binds_version_but_does_not_release_or_calibrate(self):
        from eval.provenance import digest
        report, script = run(TeamScript(), team_plan())
        self.assertEqual(report["budget"]["actual_provider_attempts"], 19)
        self.assertEqual(set(report["phases"]), set(TEAM_PHASES))
        self.assertTrue(report["material_ready"])
        acceptance = report["material_acceptance"]
        self.assertEqual(acceptance["authority"], "model")
        self.assertEqual(acceptance["corpus_hash"], digest(report["current_corpus"]))
        self.assertEqual(acceptance["protocol_hash"], digest(PROTOCOL))
        self.assertFalse(acceptance["correctness_verified"])
        self.assertTrue(report["receipt"]["material_ready"])
        self.assertFalse(report["receipt"]["scoring_ready"])
        self.assertFalse(report["receipt"]["official_release"])
        self.assertEqual(report["material_version_impact"]["by_qid"]["s001p000001"]["action"], "reanswer_and_regrade")
        self.assertTrue(all(c["payload"]["documents"][0]["content"] == AFTER for c in script.calls
                            if c["phase"] in {"question_generation", "question_review", "collection_review", "final_review", "final_collection_review"}))

    def test_team_hold_stops_questions_without_turning_opinion_into_execution_failure(self):
        report, script = run(TeamScript(team_action="hold"), team_plan())
        self.assertEqual(len(script.calls), 4)
        self.assertEqual(report["execution"]["reason"], "material_team_held")
        self.assertIsNone(report["original_snapshot"])
        self.assertEqual(report["receipt"]["original_count"], 0)
        self.assertEqual(report["current_corpus"], report["original_corpus"])
        self.assertEqual(report["phases"]["question_generation"]["execution"]["status"], "skipped")

    def test_team_global_budget_and_phase_budget_never_borrow_or_certify(self):
        for cap, phase_cap, calls in [(5, 6, 5), (18, 0, 1), (0, 6, 0)]:
            config = team_plan(cap); config["phases"]["material_team"]["max_calls"] = phase_cap
            with self.subTest(cap=cap, phase_cap=phase_cap):
                report, script = run(TeamScript(), config)
                self.assertEqual(len(script.calls), calls)
                self.assertEqual(report["budget"]["actual_provider_attempts"], calls)
                self.assertFalse(report["material_ready"])
                self.assertIsNone(report["original_snapshot"])
                if cap == 5:
                    self.assertEqual(report["current_corpus"]["sessions"][0]["docs"][0]["content"], AFTER)

    def test_team_fresh_inputs_isolated_and_editor_receives_full_collection_opinion(self):
        report, script = run(TeamScript(), team_plan())
        for call in script.calls:
            if call["phase"] in {"material_team.continuity_reader", "material_team.use_reader", "material_team.fresh_reader"}:
                self.assertEqual(set(call["payload"]), {"documents", "public_protocol"})
                self.assertNotIn("PRIVATE", json.dumps(call["payload"]))
            if call["step"].endswith("blind_read"):
                self.assertNotIn("TEAM_READER_PRIVATE_OPINION", json.dumps(call["payload"]))
                self.assertNotIn("COLLECTION_PRIVATE_OPINION", json.dumps(call["payload"]))
        editor = next(c for c in script.calls if c["phase"] == "question_edit")
        batch = next(i for i in editor["payload"]["issues"] if i["issue_id"] == "collection-review")
        self.assertIn("OVERLAP_FULL_OPINION", batch["description"])
        self.assertIn("COVERAGE_FULL_OPINION", batch["description"])
        self.assertIn("难度未实测", batch["description"])

    def test_invalid_collection_is_raw_fallible_input_not_a_drop_order(self):
        report, script = run(TeamScript(invalid_collection=True), team_plan())
        self.assertFalse(report["phases"]["collection_review"]["proposal_ready"])
        editor = next(c for c in script.calls if c["phase"] == "question_edit")
        batch = next(i for i in editor["payload"]["issues"] if i["issue_id"] == "collection-review")
        self.assertIn("unknown", batch["description"])
        self.assertEqual(report["receipt"]["original_count"], 2)

    def test_team_subphase_checkpoint_has_new_body_and_audit_failure_retains_case(self):
        seen = []
        def observer(event):
            seen.append(event["phase"])
            if event["phase"] == "material_team.edit":
                self.assertEqual(event["case_report"]["current_corpus"]["sessions"][0]["docs"][0]["content"], AFTER)
                raise OSError("nested checkpoint unavailable")
        script = TeamScript()
        with self.assertRaises(AgentCaseError) as caught:
            run(script, team_plan(), on_phase=observer)
        self.assertEqual(len(script.calls), 5)
        self.assertEqual(caught.exception.report["execution"]["status"], "audit_error")
        self.assertEqual(caught.exception.report["budget"]["actual_provider_attempts"], 5)
        partial = caught.exception.report["phases"]["material_team"]["partial_report"]
        self.assertIn("edit", partial["phases"])
        self.assertEqual(partial["candidate_corpus"]["sessions"][0]["docs"][0]["content"], AFTER)

    def test_new_mode_settings_are_explicit_and_validated_before_generation(self):
        for mutation in (lambda p: p["phases"]["material_team"]["models"].pop("decision"),
                         lambda p: p.update(material_mode="unknown"),
                         lambda p: p["phases"]["collection_review"].update(max_calls=2)):
            config = team_plan(); mutation(config); script = TeamScript()
            with self.assertRaises(ValueError):
                run(script, config)
            self.assertEqual(script.calls, [])

    def test_v2_can_review_valid_candidate_with_missing_collection_reply_without_claiming_completion(self):
        class MissingCollectionReply(TeamScript):
            def __call__(self, step, messages, **params):
                value = super().__call__(step, messages, **params)
                if params["model"] == "question_edit":
                    value["issue_responses"] = [r for r in value["issue_responses"]
                                                if r["issue_id"] != "collection-review"]
                return value
        report, script = run(MissingCollectionReply(), team_plan())
        edit = report["phases"]["question_edit"]
        self.assertTrue(edit["candidate_ready"])
        self.assertFalse(edit["proposal_ready"])
        self.assertEqual(edit["execution"]["status"], "editorial_incomplete")
        self.assertEqual(report["current_snapshot"]["questions"][0]["reference_proposal"]["answer"], "北溟保险")
        self.assertFalse(report["receipt"]["editorial_completion"]["complete"])
        self.assertIn("collection-review", report["receipt"]["editorial_completion"]["missing_issue_ids"])
        final = next(c for c in script.calls if c["phase"] == "final_review" and c["step"].endswith("adjudicate"))
        self.assertEqual(final["payload"]["reference_proposal"]["answer"], "北溟保险")
        self.assertFalse(report["receipt"]["scoring_ready"])

    def test_v1_does_not_apply_the_new_missing_reply_candidate_path(self):
        class MissingReply(Script):
            def __call__(self, step, messages, **params):
                value = super().__call__(step, messages, **params)
                if params["model"] == "question_edit":
                    value["issue_responses"] = value["issue_responses"][:1]
                return value
        report, _ = run(MissingReply())
        self.assertTrue(report["phases"]["question_edit"]["candidate_ready"])
        self.assertFalse(report["phases"]["question_edit"]["proposal_ready"])
        self.assertEqual(report["current_snapshot"]["questions"], report["original_snapshot"]["questions"])

    def test_v2_still_rejects_unknown_reply_identity_and_invalid_qid_whole_batch(self):
        class UnknownReply(TeamScript):
            def __call__(self, step, messages, **params):
                value = super().__call__(step, messages, **params)
                if params["model"] == "question_edit":
                    value["issue_responses"].append({"issue_id": "invented", "disposition": "addressed", "reason": "invalid identity"})
                return value
        for script in (UnknownReply(), TeamScript(bad_qid=True)):
            report, _ = run(script, team_plan())
            self.assertFalse(report["phases"]["question_edit"]["candidate_ready"])
            self.assertFalse(report["phases"]["question_edit"]["proposal_ready"])
            self.assertEqual(report["current_snapshot"]["questions"], report["original_snapshot"]["questions"])

    def test_final_collection_reads_only_retained_current_questions_and_keeps_both_opinions(self):
        report, script = run(TeamScript(drop_second=True), team_plan())
        before = next(c for c in script.calls if c["phase"] == "collection_review")
        after = next(c for c in script.calls if c["phase"] == "final_collection_review")
        self.assertEqual(len(before["payload"]["questions"]), 2)
        self.assertEqual(len(after["payload"]["questions"]), 1)
        self.assertEqual(after["payload"]["questions"][0]["reference_proposal"]["answer"], "北溟保险")
        self.assertEqual(set(after["payload"]), {"questions", "documents", "public_protocol"})
        wire = json.dumps(after["payload"])
        for secret in ("PRIVATE", "COLLECTION_PRIVATE_OPINION", "OVERLAP_FULL_OPINION", "DEVELOPMENT_READER_NOTE"):
            self.assertNotIn(secret, wire)
        assessment = report["receipt"]["collection_assessment"]
        self.assertEqual(assessment["original"]["snapshot_id"], report["original_snapshot"]["snapshot_id"])
        self.assertEqual(assessment["final"]["snapshot_id"], report["current_snapshot"]["snapshot_id"])
        self.assertEqual(assessment["original"]["report"], report["phases"]["collection_review"])
        self.assertEqual(assessment["final"]["report"], report["phases"]["final_collection_review"])
        self.assertFalse(assessment["improvement_verified"])
        self.assertEqual(report["receipt"]["original_count"], 2)
        self.assertEqual(sum(c["phase"] == "question_edit" for c in script.calls), 1)

    def test_final_collection_budget_exhaustion_preserves_pending_opinion_without_extra_calls(self):
        report, script = run(TeamScript(), team_plan(18))
        self.assertEqual(len(script.calls), 18)
        self.assertEqual(report["budget"]["phase_provider_attempts"]["final_collection_review"], 0)
        final = report["receipt"]["collection_assessment"]["final"]["report"]
        self.assertEqual(final["execution"]["status"], "call_budget_exhausted")
        self.assertIsNone(final["raw_output"])
        self.assertFalse(report["receipt"]["collection_assessment"]["improvement_verified"])
        self.assertEqual(report["execution"]["status"], "completed_with_execution_gaps")

    def test_final_collection_provider_failure_is_terminal_and_retains_final_item_review(self):
        class FailFinalCollection(TeamScript):
            def __call__(self, step, messages, **params):
                value = super().__call__(step, messages, **params)
                if params["model"] == "final_collection_review":
                    raise RuntimeError("final collection provider failed")
                return value
        script = FailFinalCollection()
        with self.assertRaises(AgentCaseError) as caught:
            run(script, team_plan())
        report = caught.exception.report
        validate_review(report["current_snapshot"], report["phases"]["final_review"])
        self.assertEqual(len(report["phases"]["final_review"]["items"]), 2)
        self.assertEqual(report["phases"]["final_collection_review"]["execution"]["status"], "model_error")
        self.assertEqual(report["execution"], {"status": "transport_error", "phase": "final_collection_review"})
        self.assertIsNone(report["receipt"])
        self.assertEqual(sum(c["phase"] == "question_edit" for c in script.calls), 1)
        self.assertEqual(report["budget"]["actual_provider_attempts"], len(script.calls))
        self.assertFalse(report["official_release"])

    def test_quota_valueerror_stops_queued_items_and_keeps_inflight_return_and_four_originals(self):
        """Two real injected calls overlap; only the already entered one may finish."""
        other_entered, failure_recorded = Event(), Event()
        quota_error = 'HTTP 403 {"error":{"code":"insufficient_user_quota"}}'

        class FourQuestions(TeamScript):
            def __call__(self, step, messages, **params):
                payload = json.loads(messages[1]["content"])
                if params["model"].startswith("question_review."):
                    if step.endswith("independent_reference_audit"):
                        with self.lock:
                            self.calls.append({"phase": "question_review", "step": step,
                                "payload": deepcopy(payload), "messages": deepcopy(messages), "params": deepcopy(params)})
                        if not other_entered.wait(5):
                            raise AssertionError("The second worker never entered its provider")
                        raise ValueError(quota_error)
                    if payload["question"] == QUESTIONS[1]:
                        other_entered.set()
                        if not failure_recorded.wait(5):
                            raise AssertionError("The failing provider was not observed before this return")
                value = super().__call__(step, messages, **params)
                if params["model"] == "question_generation":
                    value["questions"] = deepcopy(value["questions"] * 2)
                return value

        def record(event):
            if (event.get("phase") == "question_review"
                    and event.get("execution", {}).get("message") == quota_error):
                failure_recorded.set()

        config, script = team_plan(40), FourQuestions()
        config["question_batches"][0]["count"] = 4
        config["phases"]["question_review"].update(max_calls=12,
            reference_auditor_model="question_review.auditor", workers=2)
        with self.assertRaises(AgentCaseError) as caught:
            run(script, config, record=record)
        report = caught.exception.report
        self.assertTrue(other_entered.is_set())
        self.assertTrue(failure_recorded.is_set())
        self.assertEqual(report["execution"], {"status": "transport_error", "phase": "question_review"})
        self.assertEqual(report["fatal_failures"], [{"phase": "question_review",
            "error_type": "ValueError", "message": quota_error}])
        ids = ["s001p000001", "s001p000002", "s001p000003", "s001p000004"]
        self.assertEqual([q["qid"] for q in report["original_snapshot"]["questions"]], ids)
        generated = report["phases"]["question_generation"]
        self.assertEqual(len(generated["candidates"]), 4)
        self.assertEqual(len(generated["batch_reports"][0]["raw_output"]["questions"]), 4)
        partial = report["phases"]["question_review"]
        self.assertEqual([item["source_qid"] for item in partial["items"]], ids)
        self.assertEqual(partial["items"][0]["reference_audit"]["execution"]["message"], quota_error)
        # The second blind answer returned only after the first worker's failure
        # was recorded. Both its raw output and its decoded proposal survive.
        self.assertEqual(partial["items"][1]["blind_read_raw_output"]["answer"], "欧洲")
        self.assertEqual(partial["items"][1]["blind_read"]["answer"], "欧洲")
        self.assertTrue(all(item["blind_read"] is None for item in partial["items"][2:]))
        attempts = [a for a in report["provider_attempts"] if a["phase"] == "question_review"]
        self.assertEqual(len(attempts), 3)  # Two blind reads and one failing reference audit.
        self.assertEqual(sorted(a["status"] for a in attempts), ["provider_error", "returned", "returned"])
        self.assertEqual(report["budget"]["phase_provider_attempts"]["question_review"], 3)
        self.assertEqual(report["budget"]["actual_provider_attempts"], len(script.calls))
        self.assertEqual(len(report["provider_attempts"]), len(script.calls))
        self.assertEqual(report["budget"]["actual_provider_attempts"],
                         sum(report["budget"]["phase_provider_attempts"].values()))
        self.assertEqual([a["attempt"] for a in report["provider_attempts"]], list(range(1, len(script.calls) + 1)))
        for name in ("collection_review", "question_edit", "final_review", "final_collection_review"):
            self.assertNotIn(name, report["phases"])
            self.assertEqual(report["budget"]["phase_provider_attempts"][name], 0)
        self.assertIsNone(report["current_snapshot"])
        self.assertIsNone(report["receipt"])
        self.assertEqual(report["dispositions"], {})
        self.assertTrue(any(event.get("output", {}).get("answer") == "欧洲"
                            for event in report["records"] if isinstance(event.get("output"), dict)))

    def test_final_collection_skips_empty_retained_set_without_claiming_improvement(self):
        class DropAll(TeamScript):
            def __call__(self, step, messages, **params):
                value = super().__call__(step, messages, **params)
                if params["model"] == "question_edit":
                    for item in value["decisions"]:
                        item.update(action="drop")
                        item.pop("reference_proposal", None)
                return value
        report, script = run(DropAll(), team_plan())
        self.assertEqual(report["current_snapshot"]["questions"], [])
        self.assertEqual(report["receipt"]["original_count"], 2)
        self.assertEqual(report["receipt"]["counts"]["not_selected"], 2)
        self.assertFalse(any(c["phase"] == "final_collection_review" for c in script.calls))
        final = report["receipt"]["collection_assessment"]["final"]["report"]
        self.assertEqual(final["execution"]["status"], "skipped")
        self.assertFalse(report["receipt"]["collection_assessment"]["improvement_verified"])

    def test_legacy_mode_has_no_collection_opinions_or_new_calls(self):
        report, script = run()
        self.assertEqual(len(script.calls), 14)
        self.assertNotIn("collection_assessment", report["receipt"])
        self.assertNotIn("final_collection_review", report["phases"])

    def test_readable_material_evidence_failure_does_not_turn_model_hold_into_execution_incomplete(self):
        class BadReaderLocation(TeamScript):
            def __call__(self, step, messages, **params):
                value = super().__call__(step, messages, **params)
                if params["model"] == "team.use_reader":
                    value["evidence"][0]["location_scope"] = "content"
                return value
        report, script = run(BadReaderLocation(team_action="hold"), team_plan())
        self.assertEqual(report["phases"]["material_team"]["execution"]["status"], "completed_with_evidence_gaps")
        self.assertEqual(report["execution"]["reason"], "material_team_held")
        self.assertTrue(report["phases"]["material_team"]["evidence_gaps"])
        self.assertEqual(len(script.calls), 4)
        self.assertFalse(report["material_ready"])


class EditorEvidenceProjectionTests(unittest.TestCase):
    def fixture(self):
        from pipeline.semantic_review import locate_citations
        docs = [{"doc_id": "d000001", "content": "原记录：确认尚未完成。" * 20,
                 "date": "2025-01-01", "session": 0}]
        citation = {"doc_id": "d000001", "field": "content", "location_scope": "field",
                    "role": "counterevidence", "explanation": "反证仍须由编辑核查，不能只信标签。"}
        location = locate_citations([citation], docs)
        target = {"location_status": "ambiguous", "source_quote": "重复原句",
                  "matching_spans": [{"json_pointer": "/结论/0", "source_value_hash": "original-hash", "start": 0, "end": 4},
                                     {"json_pointer": "/结论/1", "source_value_hash": "original-hash", "start": 0, "end": 4}]}
        raw = {"resolved_text": docs[0]["content"], "claim": "模型原始意见，不是程序字段副本。"}
        audit = {"raw_output": raw, "execution": {"status": "invalid_output"}, "proposal_ready": False,
                 "format_issues": ["claim_evidence: failed"], "reference_audit_version": "v2",
                 "reference_location_policy": {"version": "v1"}, "claim_locations": [target]}
        review = {"documents": docs, "items": [{"source_qid": "q1", "blind_read": raw, "adjudication": raw,
            "execution": {"status": "audit_error"}, "stage_execution": {"blind_read": {"status": "ok"}},
            "stage_evidence_location": {"blind_read": location,
                "reference_audit": {"status": "failed", "entries": [{"status": "failed", "error": "exact failure", "target": target}],
                                    "resolved_evidence": []}}, "reference_audit": audit}], "records": []}
        collection = {"raw_output": raw, "proposal": {"limitations": ["难度未知。"]}, "execution": {"status": "ok"},
                      "format_issues": [], "resolved_evidence": deepcopy(location["resolved_evidence"])}
        return docs, review, collection

    def observations(self, issue):
        return json.loads(issue["description"].split("\n", 1)[1])

    def restore(self, value, docs):
        if isinstance(value, list):
            return [self.restore(v, docs) for v in value]
        if not isinstance(value, dict):
            return value
        out = {k: self.restore(v, docs) for k, v in value.items()}
        if out.get("resolved_text_source") == "documents":
            doc = next(d for d in docs if d["doc_id"] == out["doc_id"])
            out["resolved_text"] = doc[out["field"]]
            del out["resolved_text_source"]
        return out

    def test_complete_opinions_errors_counterevidence_and_hashes_roundtrip(self):
        from pipeline.agent_factory import _editor_issues
        docs, review, collection = self.fixture()
        original = deepcopy((review, collection, docs))
        before = _editor_issues(review, collection)
        after = _editor_issues(review, collection, documents=docs)
        self.assertEqual((review, collection, docs), original)
        for full, compact in zip(before, after):
            self.assertEqual(self.restore(self.observations(compact), docs), self.observations(full))
            self.assertEqual({k: v for k, v in full.items() if k != "description"},
                             {k: v for k, v in compact.items() if k != "description"})
        projected = self.observations(after[0])
        resolved = projected["stage_evidence_location"]["blind_read"]["entries"][0]["resolved"]
        self.assertNotIn("resolved_text", resolved)
        self.assertEqual(resolved["resolved_text_source"], "documents")
        self.assertEqual(resolved["role"], "counterevidence")
        self.assertEqual(projected["blind_read"], review["items"][0]["blind_read"])
        self.assertEqual(projected["reference_audit"]["raw_output"], collection["raw_output"])
        self.assertEqual(projected["reference_audit"]["claim_locations"], review["items"][0]["reference_audit"]["claim_locations"])

    def test_failed_entries_and_unknown_model_slots_are_never_traversed(self):
        from pipeline.agent_factory import _editor_issues
        docs, review, _ = self.fixture()
        locations = review["items"][0]["stage_evidence_location"]
        failed = {"status": "failed", "error": "do not rewrite this", "resolved": deepcopy(locations["blind_read"]["resolved_evidence"][0])}
        locations["blind_read"]["entries"].append(failed)
        locations["blind_read"]["status"] = "failed"
        locations["custom_model_output"] = deepcopy(locations["blind_read"])
        review["records"] = [{"source_qid": "q1", "event": "finished", "stage": "adjudicate",
                              "output": {"resolved_text": docs[0]["content"]}, "execution": {"status": "invalid_output"}}]
        review["items"][0]["adjudication"] = None
        projected = self.observations(_editor_issues(review, documents=docs)[0])
        self.assertEqual(projected["stage_evidence_location"]["blind_read"]["entries"][1], failed)
        self.assertEqual(projected["stage_evidence_location"]["custom_model_output"], locations["custom_model_output"])
        self.assertEqual(projected["unvalidated_stage_returns"][0]["output"], review["records"][0]["output"])
        self.assertEqual(projected["stage_evidence_location"]["blind_read"]["status"], "failed")

    def test_any_locator_mismatch_or_existing_marker_keeps_original_text(self):
        from pipeline.agent_factory import _editor_evidence_projection
        docs, review, _ = self.fixture()
        row = review["items"][0]["stage_evidence_location"]["blind_read"]["resolved_evidence"][0]
        for changes in ({"doc_id": "missing"}, {"doc_id": []}, {"field": []}, {"field": "title"},
                        {"document_hash": "stale"}, {"field_hash": "stale"}, {"resolved_text": "different"},
                        {"locator_source": "model_verbatim_quote"}, {"location_scope": "quote"},
                        {"quote": docs[0]["content"]}, {"resolved_text_source": "user supplied"}):
            with self.subTest(changes=changes):
                value = [{**deepcopy(row), **changes}]
                self.assertEqual(_editor_evidence_projection(value, docs), value)

    def test_missing_or_ambiguous_editor_documents_do_not_use_review_copy(self):
        from pipeline.agent_factory import _editor_issues
        docs, review, collection = self.fixture()
        baseline = _editor_issues(review, collection)
        for actual in (None, [], [docs[0], docs[0]], [None], {"documents": docs},
                       [{**docs[0], "content": "The editor received a different document."}]):
            with self.subTest(actual=actual):
                self.assertEqual(_editor_issues(review, collection, documents=actual), baseline)

    def test_full_length_model_quote_is_preserved(self):
        from pipeline.agent_factory import _editor_evidence_projection
        from pipeline.semantic_review import resolve_citations
        docs, _, _ = self.fixture()
        quoted = resolve_citations([{"doc_id": "d000001", "field": "content", "quote": docs[0]["content"],
                                     "role": "support", "explanation": "Model deliberately quoted this."}], docs)
        self.assertEqual(_editor_evidence_projection(quoted, docs), quoted)

    def test_actual_editor_payload_uses_current_documents_and_preserves_raw_review(self):
        report, script = run()
        editor = next(c for c in script.calls if c["phase"] == "question_edit")
        docs = editor["payload"]["documents"]
        self.assertEqual(docs[0]["content"], AFTER)
        self.assertNotIn("resolved_text_source", json.dumps(report["phases"]["question_review"]))
        for issue, original in zip(editor["payload"]["issues"], report["phases"]["question_review"]["items"]):
            observations = self.observations(issue)
            self.assertEqual(self.restore(observations["stage_evidence_location"], docs), original["stage_evidence_location"])
            self.assertIn("resolved_text_source", json.dumps(observations["stage_evidence_location"]))
            self.assertEqual(observations["blind_read"], original["blind_read"])
            self.assertEqual(observations["adjudication"], original["adjudication"])
        self.assertEqual(report["budget"]["actual_provider_attempts"], 14)
        self.assertTrue(report["receipt"]["final_review_is_fresh"])


if __name__ == "__main__":
    unittest.main()
