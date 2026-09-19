"""Offline integration of isolated opinions; fixtures are not accuracy evidence."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.quality_workflow import snapshot, fresh_review, validate_review, make_receipt
from pipeline.agent_factory import _plan
from pipeline.semantic_review import ISOLATED_VERSION
from eval.semantic_judge import SemanticJudge
from tools.run_agent_case import source_names
from quality_workflow_selftest import QUESTIONS, CORPUS, PROTOCOL, ReaderScript, run_review
from agent_factory_selftest import TeamScript, team_plan, run as run_factory


def audit_output(reference):
    return {"decision": "accept", "reason": "OFFLINE_AUDIT_OPINION: fixture only.",
        "task_requirements": ["依据材料回答题目"],
        "claims": [{"reference_part": "answer", "source_quote": reference["answer"],
            "assessment": "测试预设支持", "explanation": "测试固定意见", "evidence": []}],
        "task_coverage": "测试预设已回答", "substantive_defects": [], "acceptable_brevity": [],
        "editorial_suggestions": [], "suggested_revision": "", "limitations": [], "evidence": []}


def attach_targets(result, reference):
    for row in result["original_answer_review"]:
        row.update(target_scope="quoted_text", reference_targets=[{
            "reference_part": "answer", "source_quote": reference["answer"]}])
    for row in result["original_rationale_review"]["claims"]:
        row.update(target_scope="quoted_text", reference_targets=[{
            "reference_part": "rationale", "source_quote": reference["rationale"]}])
    result["reference_audit_response"] = "测试预设：已对照参考审计的全部意见，不用模型一致代替材料。"
    return result


class IsolatedScript(ReaderScript):
    def __init__(self, *, bad_location=False):
        super().__init__(); self.bad_location = bad_location

    def __call__(self, step, messages, **params):
        payload = json.loads(messages[1]["content"])
        if "original_reference" in payload:
            self.calls.append({"stage": "reference_audit", "payload": payload,
                "messages": deepcopy(messages), "params": deepcopy(params)})
            result = audit_output(payload["original_reference"])
            if self.bad_location:
                result["claims"][0]["evidence"] = [{"doc_id": "d000001", "field": "content",
                    "location_scope": "quote", "role": "support", "explanation": "Missing actual quote"}]
            return result
        result = super().__call__(step, messages, **params)
        if step.endswith("adjudicate"):
            attach_targets(result, payload["reference_proposal"])
        return result


class IsolatedWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.frozen = snapshot(QUESTIONS, CORPUS, PROTOCOL)

    def review(self, script=None, budget=6):
        return run_review(self.frozen, script or IsolatedScript(), max_calls=budget,
            reference_auditor_model="offline-independent-auditor")

    def test_three_real_roles_reach_receipt_and_keep_blind_isolation(self):
        script = IsolatedScript(); report = self.review(script)
        self.assertEqual(report["version"], ISOLATED_VERSION)
        self.assertEqual(report["calls_used"], 6)
        self.assertEqual(report["budget"]["per_candidate_calls"], [3, 3])
        validate_review(self.frozen, report)
        self.assertEqual(make_receipt(self.frozen, self.frozen, report)["counts"]["model_review_eligible"], 2)
        for call in script.calls:
            self.assertEqual(call["params"]["retries"], 1)
            payload = call["payload"]
            self.assertNotIn("PRIVATE", json.dumps(payload))
            if call["stage"] == "blind_read":
                self.assertNotIn("reference_proposal", payload)
                self.assertNotIn("original_reference", payload)
            elif call["stage"] == "reference_audit":
                self.assertNotIn("blind_read", payload)
                self.assertNotIn("major_requirements", payload)
            else:
                self.assertIn("OFFLINE_AUDIT_OPINION", json.dumps(payload))

    def test_physical_call_budget_counts_third_role(self):
        for budget in range(7):
            with self.subTest(budget=budget):
                script = IsolatedScript(); report = self.review(script, budget)
                self.assertEqual(len(script.calls), budget)
                self.assertEqual(report["calls_used"], budget)
                self.assertEqual(sum(i["review_state"] == "completed" for i in report["items"]), budget // 3)
                validate_review(self.frozen, report)

    def test_failed_citation_is_preserved_and_blocks_certification_not_question(self):
        script = IsolatedScript(bad_location=True); report = self.review(script)
        validate_review(self.frozen, report)
        for item in report["items"]:
            self.assertEqual(item["item_validity"], "valid")
            self.assertEqual(item["review_state"], "pending")
            self.assertFalse(item["reference_audit"]["proposal_ready"])
            self.assertEqual(item["reference_audit"]["raw_output"]["decision"], "accept")
        self.assertEqual(len(script.calls), 6)
        self.assertEqual(make_receipt(self.frozen, self.frozen, report)["counts"]["unresolved"], 2)

    def test_completed_cache_rechecks_actual_audit_and_target_spans(self):
        report = self.review()
        def wrong_target(item):
            item["adjudication"]["original_answer_review"][0]["reference_targets"][0]["source_quote"] = "OTHER_ANSWER"
        changes = [
            lambda item: item["reference_audit"]["raw_output"]["claims"][0].update(source_quote="OTHER_ANSWER"),
            lambda item: item["reference_audit"]["binding"].update(input_hash="stale"),
            lambda item: item["reference_audit"].update(reference_audit_version="old"),
            lambda item: item["stage_evidence_location"].pop("reference_audit"),
            lambda item: item["adjudication"].update(reference_audit_response=""), wrong_target]
        for change in changes:
            with self.subTest(change=change):
                altered = deepcopy(report); change(altered["items"][0])
                with self.assertRaises(ValueError): validate_review(self.frozen, altered)

    def test_grade_sees_full_audit_opinion_without_duplicate_request_corpus(self):
        report = self.review(); requests = []
        def fake(step, messages, **kwargs):
            requests.append(json.loads(messages[1]["content"]))
            return {"__error__": "test ends after payload capture"}
        judge = SemanticJudge(report, self.frozen["questions"], self.frozen["corpus"], PROTOCOL,
            model="offline-grader", chat_json=fake)
        judge(self.frozen["questions"][0], "北溟保险")
        opinion = requests[0]["reference_review"]["independent_reference_audit"]
        self.assertIn("OFFLINE_AUDIT_OPINION", opinion["raw_output"]["reason"])
        self.assertNotIn("records", opinion)

    def test_incomplete_audit_never_reaches_grading_call(self):
        report = self.review(IsolatedScript(bad_location=True))
        def forbidden(*a, **k): raise AssertionError("must not grade pending item")
        judge = SemanticJudge(report, self.frozen["questions"], self.frozen["corpus"], PROTOCOL,
            model="offline-grader", chat_json=forbidden)
        self.assertEqual(judge(self.frozen["questions"][0], "北溟保险")["verdict"], "uncertain")

    def test_plan_source_set_freezes_selected_auditor(self):
        plan = team_plan()
        self.assertNotIn("pipeline/reference_audit.py", source_names(plan))
        for name in ("question_review", "final_review"):
            plan["phases"][name]["reference_auditor_model"] = "independent-auditor"
        _plan(plan)
        self.assertIn("pipeline/reference_audit.py", source_names(plan))
        plan["phases"]["final_review"]["reference_auditor_model"] = ""
        with self.assertRaises(ValueError): _plan(plan)


class IsolatedFactoryScript(TeamScript):
    def __call__(self, step, messages, **params):
        payload = json.loads(messages[1]["content"])
        if "original_reference" in payload:
            self.calls.append({"phase": params["model"].split(".")[0], "step": step,
                "payload": deepcopy(payload), "params": deepcopy(params), "messages": deepcopy(messages)})
            return audit_output(payload["original_reference"])
        result = super().__call__(step, messages, **params)
        if step.endswith("adjudicate"):
            attach_targets(result, payload["reference_proposal"])
        return result


class IsolatedFactoryTests(unittest.TestCase):
    def test_later_success_does_not_erase_prior_agent_failure_in_execution_summary(self):
        class FailingAuditor(IsolatedFactoryScript):
            def __call__(self, step, messages, **params):
                result = super().__call__(step, messages, **params)
                return {"__error__": "OFFLINE_B_FAILED"} if "original_reference" in json.loads(messages[1]["content"]) else result
        plan = team_plan(23)
        for name in ("question_review", "final_review"):
            plan["phases"][name].update(reference_auditor_model=name + ".auditor", max_calls=6)
        result, _ = run_factory(FailingAuditor(), plan)
        self.assertEqual(result["execution"]["status"], "completed_with_execution_gaps")
        gaps = [g for g in result["execution"]["gaps"] if g.get("stage") == "reference_audit"]
        self.assertEqual(len(gaps), 4)
        self.assertTrue(all(g["execution"]["status"] == "model_error" for g in gaps))
        self.assertEqual(result["receipt"]["counts"]["unresolved"], 2)

    def test_whole_factory_preserves_audits_through_edit_and_fresh_final_review(self):
        plan = team_plan(23)
        for name in ("question_review", "final_review"):
            plan["phases"][name].update(reference_auditor_model=name + ".auditor", max_calls=6)
        result, script = run_factory(IsolatedFactoryScript(), plan)
        self.assertEqual(result["budget"]["actual_provider_attempts"], 23)
        final = result["phases"]["final_review"]
        self.assertEqual(final["version"], ISOLATED_VERSION)
        validate_review(result["current_snapshot"], final)
        editor = next(c for c in script.calls if c["phase"] == "question_edit")
        self.assertIn("OFFLINE_AUDIT_OPINION", json.dumps(editor["payload"]["issues"]))
        self.assertEqual(result["receipt"]["counts"]["model_review_eligible"], 2)
        self.assertFalse(result["receipt"]["scoring_ready"])

    def test_cli_freezes_auditor_source_and_persists_all_three_stages(self):
        import hashlib
        from tools import run_agent_case as cli
        root = Path(__file__).resolve().parents[1]
        plan = team_plan(23)
        for name in ("question_review", "final_review"):
            plan["phases"][name].update(reference_auditor_model=name + ".auditor", max_calls=6)
        def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            for name in ("seed.json", "design_intent.json", "question_intent.json"):
                (case / name).write_text('{"intent":"PRIVATE_TEST"}', encoding="utf-8")
            (case / "protocol.txt").write_text(PROTOCOL, encoding="utf-8")
            value = {"version": cli.TEAM_PLAN_VERSION, "result_scope": "research_only", "factory_plan": plan,
                "input_sha256": {name: sha(case / name) for name in cli.INPUTS if name != "plan.json"},
                "source_sha256": {name: sha(root / name) for name in cli.source_names(plan)}}
            (case / "plan.json").write_text(json.dumps(value), encoding="utf-8")
            with patch.object(cli, "provider", return_value=(IsolatedFactoryScript(), lambda event: None, deepcopy)):
                cli.run(case, execute=True)
            stored = json.loads((case / "execution/phases/final_review/report.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["version"], ISOLATED_VERSION)
            self.assertEqual(set(stored["items"][0]["stage_execution"]), {"blind_read", "reference_audit", "adjudicate"})
            self.assertEqual(sha(case / "execution/source_snapshot/pipeline/reference_audit.py"),
                value["source_sha256"]["pipeline/reference_audit.py"])


if __name__ == "__main__": unittest.main()
