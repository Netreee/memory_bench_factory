"""Injected v8 integration checks; scripted opinions are not accuracy evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.quality_workflow import snapshot, fresh_review, validate_review
from pipeline.semantic_review import prepare_review, ISOLATED_VERSION, fingerprint
from pipeline.reference_locations import reference_value_hash
from pipeline.reference_audit import audit_reference
from pipeline.agent_editing import propose_question_revisions
from eval.semantic_judge import SemanticJudge
from semantic_judge_selftest import review_output, grade_output

CORPUS = [{"content": "OFFLINE_FIXTURE corpus; no real correctness claim."}]
PROTOCOL = "Only the supplied public material; OFFLINE_FIXTURE."
REAL_REPORT = ROOT / "output/implementation_bc_agent_loop_20260917/prospective_reasoning_case_8docs_4q/execution/failed_report.json"
REAL_SHA = "c7cbb1c8c959811009667a8ff352fe2137e737f8c8e8254dd1126f8f9246cf5b"


def anchor(value):
    """Choose a technical source anchor, not a fact to mark correct."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        if not value:
            return "{}"
        return next((found for child in value.values() if (found := anchor(child))), None)
    if isinstance(value, list):
        if not value:
            return "[]"
        return next((found for child in value if (found := anchor(child))), None)
    return json.dumps(value, allow_nan=False)


def questions(values):
    return [{"qid": "mixed-" + str(i), "question": "OFFLINE_FIXTURE task " + str(i),
             "reference_proposal": {"answer": deepcopy(value), "rationale": "OFFLINE_FIXTURE rationale."}}
            for i, value in enumerate(values)]


class StructuredScript:
    def __init__(self, *, bad_quote=False):
        self.calls = []
        self.bad_quote = bad_quote

    def __call__(self, step, messages, **params):
        payload = json.loads(messages[1]["content"])
        self.calls.append({"step": step, "payload": payload, "params": deepcopy(params)})
        if step == "semantic_judge.answer":
            return grade_output(evidence=[], reasoning="OFFLINE_FIXTURE only.")
        if "original_reference" in payload:
            reference = payload["original_reference"]
            quote = "ABSENT_OFFLINE_QUOTE" if self.bad_quote else anchor(reference["answer"])
            return {"decision": "accept", "reason": "OFFLINE_FIXTURE; not real semantic approval.",
                "task_requirements": ["OFFLINE_FIXTURE"],
                "claims": [{"reference_part": "answer", "source_quote": quote,
                    "assessment": "OFFLINE_FIXTURE", "explanation": "OFFLINE_FIXTURE", "evidence": []}] if quote else [],
                "task_coverage": "OFFLINE_FIXTURE", "substantive_defects": [], "acceptable_brevity": [],
                "editorial_suggestions": [], "suggested_revision": "", "limitations": [], "evidence": []}
        if step.endswith("blind_read"):
            return review_output("blind_read", answer="BLIND_ONLY_SENTINEL", evidence=[],
                                 reasoning="OFFLINE_FIXTURE", interpretation="OFFLINE_FIXTURE")
        reference = payload["reference_proposal"]
        quote = anchor(reference["answer"])
        result = review_output(evidence=[], reasoning="OFFLINE_FIXTURE", interpretation="OFFLINE_FIXTURE")
        result["original_answer_review"] = ([{"requirement": "OFFLINE_FIXTURE",
            "assessment": "OFFLINE_FIXTURE", "explanation": "OFFLINE_FIXTURE",
            "target_scope": "quoted_text", "reference_targets": [{"reference_part": "answer", "source_quote": quote}]}]
            if quote else [])
        rationale = reference["rationale"]
        result["original_rationale_review"] = {"status": "reviewed" if rationale else "not_provided",
            "claims": [{"claim": "OFFLINE_FIXTURE", "assessment": "OFFLINE_FIXTURE", "explanation": "OFFLINE_FIXTURE",
                "target_scope": "quoted_text", "reference_targets": [{"reference_part": "rationale", "source_quote": rationale}]}]
                if rationale else [], "limitations": []}
        result["reference_audit_response"] = "OFFLINE_FIXTURE response, not a semantic endorsement."
        return result


def run_review(frozen, script=None, budget=None, workers=1):
    script = script or StructuredScript()
    result = fresh_review(frozen, reader_model="offline-reader", reviewer_model="offline-critic",
        reference_auditor_model="offline-auditor", chat_json=script,
        max_calls=3 * len(frozen["questions"]) if budget is None else budget,
        max_input_chars=500000, max_tokens=16384, workers=workers)
    return result, script


class StructuredWorkflowTests(unittest.TestCase):
    def test_exact_real_four_dict_references_reach_review_and_grader_unchanged(self):
        if not REAL_REPORT.exists():
            self.skipTest("Local development artifact is not distributed with source")
        self.assertEqual(hashlib.sha256(REAL_REPORT.read_bytes()).hexdigest(), REAL_SHA)
        frozen = json.loads(REAL_REPORT.read_text(encoding="utf-8"))["original_snapshot"]
        before = deepcopy(frozen)
        self.assertEqual(len(frozen["questions"]), 4)
        self.assertTrue(all(type(q["reference_proposal"]["answer"]) is dict for q in frozen["questions"]))
        report, script = run_review(frozen)
        self.assertEqual(report["version"], "semantic-question-shadow/v8")
        self.assertEqual(report["calls_used"], 12)
        validate_review(frozen, report)
        judge = SemanticJudge(report, frozen["questions"], frozen["corpus"], frozen["protocol"],
                              model="offline-grader", chat_json=script, max_calls=4, max_input_chars=500000)
        for question in frozen["questions"]:
            self.assertEqual(judge(question, "OFFLINE_FIXTURE solver response")["execution_status"], "ok")
        self.assertEqual(len(script.calls), 16)
        self.assertEqual(frozen, before)
        originals = {q["question"]: q["reference_proposal"] for q in frozen["questions"]}
        for call in script.calls:
            payload = call["payload"]
            self.assertEqual(call["params"]["retries"], 1)
            if call["step"].endswith("blind_read"):
                self.assertNotIn("reference_proposal", payload)
                self.assertNotIn("original_reference", payload)
            elif "original_reference" in payload:
                self.assertEqual(payload["original_reference"], originals[payload["question"]])
                self.assertNotIn("BLIND_ONLY_SENTINEL", json.dumps(payload))
            elif call["step"] == "semantic_judge.answer":
                reference = payload["reference_review"]
                self.assertEqual(reference["reference_proposal"], originals[payload["question"]])
                self.assertIn("reference_location_policy", reference["independent_reference_audit"])

    def test_mixed_batch_partitions_have_uniform_v8_and_typed_originals(self):
        values = ["literal text", {"a/b~c": ["same", "same"]}, ["nested"], 1, True, None, {}, [], ""]
        frozen = snapshot(questions(values), CORPUS, PROTOCOL)
        for workers in (1, 2):
            report, _ = run_review(frozen, workers=workers)
            validate_review(frozen, report)
            self.assertEqual(report["version"], ISOLATED_VERSION)
            self.assertEqual(report["calls_used"], 3 * len(values))
            for item, value in zip(report["items"], values):
                self.assertEqual(item["binding"]["version"], ISOLATED_VERSION)
                self.assertEqual(item["review_state"], "completed")
                self.assertEqual(reference_value_hash(item["reference_proposal"]["answer"]), reference_value_hash(value))

    def test_editor_structured_revision_returns_to_fresh_review_and_grader(self):
        original = snapshot(questions([{"state": "before"}, "text"]), CORPUS, PROTOCOL)
        initial, _ = run_review(original)
        changed = {"answer": {"state": ["after", 1, None]}, "rationale": "OFFLINE revised."}
        def edit(step, messages, **params):
            payload = json.loads(messages[1]["content"])
            self.assertEqual(payload["questions"][0]["reference_proposal"], original["questions"][0]["reference_proposal"])
            return {"decisions": [{"qid": q["qid"], "action": "revise" if i == 0 else "keep",
                "reason": "OFFLINE_FIXTURE", **({"reference_proposal": changed} if i == 0 else {})}
                for i, q in enumerate(payload["questions"])], "issue_responses": []}
        edited = propose_question_revisions(original["questions"], CORPUS, PROTOCOL, [],
                                            model="offline-editor", chat_json=edit)
        self.assertTrue(edited["candidate_ready"])
        current = snapshot(edited["proposed_questions"], CORPUS, PROTOCOL, parent=original)
        with self.assertRaises(ValueError):
            validate_review(current, initial)
        final, script = run_review(current)
        validate_review(current, final)
        self.assertEqual(current["questions"][0]["reference_proposal"], changed)
        self.assertEqual(original["questions"][0]["reference_proposal"]["answer"], {"state": "before"})
        self.assertEqual(current["identities"]["mixed-1"], original["identities"]["mixed-1"])
        judge = SemanticJudge(final, current["questions"], CORPUS, PROTOCOL,
                              model="offline-grader", chat_json=script)
        self.assertEqual(judge(current["questions"][0], "OFFLINE_FIXTURE")["execution_status"], "ok")

    def test_typed_attribution_tampering_is_rejected(self):
        frozen = snapshot(questions([{"a": ["same", "same"]}]), CORPUS, PROTOCOL)
        report, _ = run_review(frozen)
        def location(item):
            return item["reference_audit"]["claim_locations"][0]["matching_spans"][0]
        changes = [lambda item: location(item).update(json_pointer="/wrong"),
            lambda item: location(item).update(source_kind="object_key"),
            lambda item: location(item).update(source_value_hash="0" * 64),
            lambda item: location(item).update(start=2),
            lambda item: item["reference_audit"]["claim_locations"][0].update(location_status="unique"),
            lambda item: item["reference_audit"]["reference_location_policy"].update(version="old"),
            lambda item: item["binding"]["reference_location_policy"].update(implementation_hash="old"),
            lambda item: item["reference_proposal"].update(answer='{"a":["same","same"]}'),
            lambda item: item["reference_target_locations"][0]["matching_spans"].pop()]
        for change in changes:
            with self.subTest(change=change):
                corrupted = deepcopy(report)
                change(corrupted["items"][0])
                with self.assertRaises(ValueError):
                    validate_review(frozen, corrupted)

    def test_bad_structured_quote_preserves_failed_location_and_is_not_certified(self):
        frozen = snapshot(questions([{"a": "real"}]), CORPUS, PROTOCOL)
        report, script = run_review(frozen, StructuredScript(bad_quote=True))
        item = report["items"][0]
        self.assertEqual(item["reference_audit"]["claim_locations"][0]["location_status"], "failed")
        self.assertEqual(item["review_state"], "pending")
        self.assertEqual(item["item_validity"], "valid")  # Failure is not a bad-question verdict.
        self.assertEqual(script.calls[-1]["payload"]["reference_audit"]["claim_locations"][0]["matching_spans"], [])
        validate_review(frozen, report)

    def test_mixed_call_budgets_have_no_hidden_attempt(self):
        frozen = snapshot(questions([{"a": "value"}, None]), CORPUS, PROTOCOL)
        for budget in range(7):
            report, script = run_review(frozen, budget=budget)
            self.assertEqual(report["calls_used"], budget)
            self.assertEqual(len(script.calls), budget)
            validate_review(frozen, report)

    def test_invalid_values_rejected_before_copy_or_provider(self):
        def forbidden(*args, **kwargs):
            self.fail("Invalid input cannot call provider")
        for value in ({1: "coercion forbidden"}, float("nan"), {"x": object()}):
            question = questions([value])[0]
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError):
                    prepare_review([question], CORPUS, PROTOCOL, reviewer_model="r", reference_auditor_model="a")
                with self.assertRaises(ValueError):
                    audit_reference(question, CORPUS, PROTOCOL, model="a", chat_json=forbidden)

    def test_audit_output_identity_includes_actual_policy_without_posthoc_binding_mutation(self):
        q = questions([{"a": "x"}])[0]
        result = audit_reference(q, CORPUS, PROTOCOL, model="a", chat_json=StructuredScript())
        from pipeline.agent_editing import digest
        self.assertEqual(result["output_hash"], digest({"binding": result["binding"], "raw_output": result["raw_output"]}))
        self.assertEqual(result["inputs"]["reference_location_policy"], result["reference_location_policy"])
        self.assertEqual(result["binding"]["input_hash"], digest(result["inputs"]))

    def test_full_factory_keeps_structured_generation_and_editor_revision(self):
        from agent_factory_selftest import TeamScript, team_plan, run as run_factory
        scripted_review = StructuredScript()
        class FactoryScript(TeamScript):
            def __call__(self, step, messages, **params):
                phase = params["model"].split(".")[0]
                if phase in {"question_review", "final_review"}:
                    return scripted_review(step, messages, **params)
                result = super().__call__(step, messages, **params)
                if phase == "question_generation":
                    for q in result["questions"]:
                        q["reference_proposal"]["answer"] = {"conclusion": q["reference_proposal"]["answer"]}
                elif phase == "question_edit":
                    result["decisions"][0]["reference_proposal"]["answer"] = {"conclusion": ["revised", None]}
                return result
        plan = team_plan(23)
        for phase in ("question_review", "final_review"):
            plan["phases"][phase].update(reference_auditor_model=phase + ".auditor", max_calls=6)
        result, script = run_factory(FactoryScript(), plan)
        self.assertEqual(result["budget"]["actual_provider_attempts"], 23)
        self.assertTrue(all(type(q["reference_proposal"]["answer"]) is dict
                            for q in result["current_snapshot"]["questions"]))
        validate_review(result["current_snapshot"], result["phases"]["final_review"])
        self.assertEqual(result["receipt"]["counts"]["model_review_eligible"], 2)
        self.assertFalse(result["receipt"]["official_release"])
        editor = next(call for call in script.calls if call["phase"] == "question_edit")
        self.assertIn("OFFLINE_FIXTURE", json.dumps(editor["payload"]["issues"]))

    def test_optional_helper_is_frozen_and_default_v6_never_imports_it(self):
        from agent_factory_selftest import team_plan
        from tools.run_agent_case import source_names
        plan = team_plan()
        # The shared locator is also used by the A-policy answer reader;
        # only the independent reference auditor remains optional.
        self.assertIn("pipeline/reference_locations.py", source_names(plan))
        self.assertNotIn("pipeline/reference_audit.py", source_names(plan))
        plan["phases"]["question_review"]["reference_auditor_model"] = "audit"
        self.assertIn("pipeline/reference_locations.py", source_names(plan))
        self.assertIn("pipeline/reference_audit.py", source_names(plan))
        script = "\n".join([
            "import sys",
            "from pipeline.semantic_review import prepare_review",
            "r=prepare_review([{'question':'q','reference_proposal':{'answer':{'a':1},'rationale':'r'}}],[{'content':'d'}],'p',reviewer_model='r')",
            "assert r['version']=='semantic-question-shadow/v6'",
            "assert 'pipeline.reference_locations' not in sys.modules",
            "assert 'pipeline.reference_audit' not in sys.modules",
            "assert 'config' not in sys.modules",
        ])
        result = subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
