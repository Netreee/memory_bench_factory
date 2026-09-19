"""Offline version, isolation, budget and semantic-authority checks."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import material_team as team
from pipeline.agent_editing import AuditRecordError

CORPUS = [{"doc_id": "original_id", "date": "2026-01-01", "content": "尚未收到答复。",
           "title": "PRIVATE TITLE", "gold": "PRIVATE ANSWER"}]
MODELS = {phase: "model-" + phase for phase in team.PHASES}


def reading():
    return {"assessment": "公开记录允许事情未决。", "findings": [],
            "preserved_uncertainties": ["未答复。"], "limitations": [], "evidence": []}


def triage(action="accept"):
    return {"action": action, "reason": "有据保留未知。", "issues": [],
            "opinion_review": [{"reader": "both", "assessment": "未决非错误。"}],
            "preserved_uncertainties": ["未答复。"], "limitations": [], "evidence": []}


def decision(action="accept"):
    return {"action": action, "reason": "适合询问已知边界。", "change_review": [],
            "opinion_review": [], "unresolved_findings": [], "preserved_uncertainties": ["未答复。"],
            "limitations": [], "evidence": [], "original_evidence": []}


class Fake:
    def __init__(self, outputs=None):
        self.outputs = outputs or [reading(), reading(), triage(), reading(), decision()]
        self.calls = []

    def __call__(self, step, messages, **params):
        self.calls.append((step, deepcopy(messages), deepcopy(params)))
        value = self.outputs[len(self.calls) - 1]
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)


def run(fake, **kwargs):
    return team.run_material_team(CORPUS, "只依据材料，允许未知。", models=MODELS,
        chat_json=fake, **kwargs)


class MaterialTeamTests(unittest.TestCase):
    def test_accept_is_attributed_opinion_and_unknown_preserved(self):
        fake = Fake()
        result = run(fake)
        self.assertEqual(result["actual_provider_attempts"], 5)
        self.assertTrue(result["accepted_for_question_generation"])
        self.assertFalse(result["acceptance"]["correctness_verified"])
        self.assertEqual(result["acceptance"]["authority"], "model")
        self.assertEqual(result["acceptance"]["type"], "fallible_model_acceptance")
        self.assertFalse(result["official_release"])
        self.assertEqual(result["original_corpus"][0]["content"], "尚未收到答复。")

    def test_independent_readers_never_see_other_opinions(self):
        fake = Fake(); run(fake)
        for index in (0, 1, 3):
            payload = json.loads(fake.calls[index][1][1]["content"])
            self.assertEqual(set(payload), {"documents", "public_protocol"})
            self.assertNotIn("PRIVATE", json.dumps(payload))
        self.assertIn("reader_opinions", json.loads(fake.calls[2][1][1]["content"]))

    def test_hold_stops_without_writer_or_question_generation(self):
        result = run(Fake([reading(), reading(), triage("hold")]))
        self.assertEqual(result["actual_provider_attempts"], 3)
        self.assertFalse(result["accepted_for_question_generation"])
        self.assertNotIn("edit", result["phases"])

    def test_budget_zero_no_calls(self):
        fake = Fake(); result = run(fake, max_calls=0)
        self.assertFalse(fake.calls)
        self.assertEqual(result["execution"]["status"], "incomplete")

    def test_budget_stops_before_final_decision(self):
        fake = Fake(); result = run(fake, max_calls=4)
        self.assertEqual(len(fake.calls), 4)
        self.assertFalse(result["accepted_for_question_generation"])
        self.assertEqual(result["execution"]["phase"], "decision")

    def test_provider_failure_retains_raw_and_no_retry(self):
        fake = Fake([RuntimeError("unavailable")]); result = run(fake)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(result["phases"]["continuity_reader"]["execution"]["status"], "model_error")

    def test_false_quote_not_downgraded_to_field(self):
        value = reading()
        value["evidence"] = [{"doc_id": "d000001", "field": "content", "location_scope": "quote",
            "quote": "已收到答复。", "role": "support", "explanation": "错误引用。"}]
        fake = Fake([value, reading(), triage(), reading(), decision()])
        result = run(fake)
        self.assertTrue(result["accepted_for_question_generation"])
        self.assertEqual(result["execution"]["status"], "completed_with_evidence_gaps")
        self.assertEqual(result["phases"]["continuity_reader"]["raw_output"], value)
        self.assertFalse(result["phases"]["continuity_reader"]["proposal_ready"])
        prior = json.loads(fake.calls[2][1][1]["content"])["reader_opinions"]["continuity_reader"]
        self.assertEqual(prior["opinion"], value)
        self.assertEqual(prior["evidence_location"]["status"], "failed")

    def test_final_decision_bad_quote_cannot_accept(self):
        bad = decision()
        bad["evidence"] = [{"doc_id": "d000001", "field": "content", "location_scope": "quote",
            "quote": "已收到答复。", "role": "support", "explanation": "错误引用。"}]
        result = run(Fake([reading(), reading(), triage(), reading(), bad]))
        self.assertFalse(result["accepted_for_question_generation"])
        self.assertEqual(result["execution"]["phase"], "decision")

    def test_reader_shape_error_still_stops(self):
        value = reading(); value["findings"] = "unreadable structure"
        fake = Fake([value]); result = run(fake)
        self.assertEqual(len(fake.calls), 1)
        self.assertFalse(result["phases"]["continuity_reader"]["opinion_readable"])

    def test_callback_failure_stops(self):
        fake = Fake()
        def fail(event):
            raise RuntimeError("disk full")
        with self.assertRaises(AuditRecordError) as caught:
            run(fake, on_phase=fail)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(caught.exception.report["execution"]["status"], "audit_error")

    def test_callback_cannot_mutate_working_report(self):
        def mutate(event):
            event["case_report"]["candidate_corpus"].clear()
            event["phase_report"].clear()
        result = run(Fake(), on_phase=mutate)
        self.assertEqual(len(result["candidate_corpus"]), 1)

    def test_revision_is_candidate_until_independent_decision(self):
        t = triage("revise")
        t["issues"] = [{"issue_id": "x", "description": "角色说法需澄清。", "evidence": []}]
        edit = {"action": "revise", "reason": "保留范围。",
            "document_edits": [{"doc_id": "d000001", "content": "记录中尚未收到答复；不说明实际有无答复。"}],
            "issue_responses": [{"issue_id": "x", "disposition": "addressed", "reason": "限定本记录。"}]}
        fake = Fake([reading(), reading(), t, edit, reading(), decision("hold")])
        result = run(fake)
        self.assertEqual(len(fake.calls), 6)
        self.assertNotEqual(result["candidate_corpus"], result["original_corpus"])
        self.assertFalse(result["accepted_for_question_generation"])
        final_payload = json.loads(fake.calls[-1][1][1]["content"])
        self.assertEqual(final_payload["original_documents"][0]["content"], "尚未收到答复。")
        self.assertIn("记录中", final_payload["documents"][0]["content"])

    def test_all_calls_single_attempt(self):
        fake = Fake(); run(fake)
        self.assertTrue(all(p["retries"] == 1 and p["strict_json"] for _, _, p in fake.calls))

    def test_record_failure_preserves_outer_history_and_attempts(self):
        fake = Fake()
        def fail(event):
            if event["event"] == "finished" and len(fake.calls) == 2:
                raise OSError("audit write failed")
        with self.assertRaises(AuditRecordError) as caught:
            run(fake, record=fail)
        result = caught.exception.report
        self.assertEqual(result["actual_provider_attempts"], 2)
        self.assertIn("continuity_reader", result["phases"])
        self.assertEqual(result["phases"]["use_reader"]["raw_output"], reading())
        self.assertEqual(result["execution"]["status"], "audit_error")

    def test_edited_and_final_checkpoints_contain_current_version_and_decision(self):
        t = triage("revise")
        t["issues"] = [{"issue_id": "x", "description": "Clarify scope.", "evidence": []}]
        edit = {"action": "revise", "reason": "Scope.",
            "document_edits": [{"doc_id": "d000001", "content": "仍未有记录。"}],
            "issue_responses": [{"issue_id": "x", "disposition": "addressed", "reason": "Scope clarified."}]}
        checkpoints = {}
        run(Fake([reading(), reading(), t, edit, reading(), decision()]),
            on_phase=lambda event: checkpoints.update({event["phase"]: event["case_report"]}))
        self.assertEqual(checkpoints["edit"]["candidate_corpus"][0]["content"], "仍未有记录。")
        self.assertTrue(checkpoints["decision"]["accepted_for_question_generation"])
        self.assertEqual(checkpoints["decision"]["execution"]["status"], "completed")

    def test_external_models_mutation_cannot_change_frozen_call_models(self):
        models = deepcopy(MODELS)
        fake = Fake()
        team.run_material_team(CORPUS, "只依据公开材料。", models=models, chat_json=fake,
            on_phase=lambda event: models.update({name: "mutated" for name in models}))
        self.assertNotIn("mutated", [params["model"] for _, _, params in fake.calls])


if __name__ == "__main__":
    unittest.main()
