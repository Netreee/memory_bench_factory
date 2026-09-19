"""Offline research evaluation integration; scripted grades are not accuracy evidence.

Runs the actual CLI preparation, FullContext, answer seam, SemanticJudge,
reassessment closure, filter, provider and config transport against FakeSDK.
The external calibration gate is isolated except for explicit rejection tests;
these fixtures do not certify a real calibration or freeze a research run.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_agent_evaluation as cli
from pipeline.quality_workflow import snapshot
from pipeline.semantic_review import review_questions, visible_documents
from eval import qa_cache
from eval.grading import is_scored
from llm_transport import (VERSION as TRANSPORT_VERSION, transport_fingerprint,
                           build_request_parameters, resolve_profile)
from config_transport_selftest import FakeSDK, load_config
from llm_trace_selftest import response


PROTOCOL = "仅依据公开正文回答题目，必要时解释不确定性。"
CORPUS = {"private": "PRIVATE_CORPUS_METADATA", "sessions": [{
    "session_id": 1, "date": "2024-03-20", "docs": [{
        "title": "PRIVATE_TITLE", "content": "报告于2024年3月20日发布。",
        "refs": "PRIVATE_REFERENCE_METADATA", "review": "PRIVATE_REVIEW_METADATA"}]}]}


def transport():
    return {"version": TRANSPORT_VERSION, "default_profile": "legacy",
        "model_profiles": {"solver-a": "a", "solver-b": "b", "grader": "g"},
        "profiles": {
            "a": {"token_limit_parameter": "max_tokens", "response_format": {"type": "json_object"},
                  "http_timeout_seconds": 3, "deadline_seconds": 3},
            "b": {"token_limit_parameter": "max_completion_tokens", "reasoning_effort": "medium",
                  "omit_parameters": ["temperature", "top_p"], "response_format": {"type": "json_object"},
                  "http_timeout_seconds": 4, "deadline_seconds": 4},
            "g": {"token_limit_parameter": "max_completion_tokens", "reasoning_effort": "high",
                  "omit_parameters": ["temperature", "top_p"], "response_format": {"type": "json_object"},
                  "http_timeout_seconds": 5, "deadline_seconds": 5}}}


def first_text(value):
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict):
        values = value.values()
    elif isinstance(value, list):
        values = value
    else:
        return None
    for child in values:
        found = first_text(child)
        if found:
            return found
    return None


@contextmanager
def configured_module(value):
    """Replace only config; do not unload newly imported NumPy extension modules."""
    missing = object()
    previous = sys.modules.get("config", missing)
    sys.modules["config"] = value
    try:
        yield
    finally:
        if previous is missing:
            sys.modules.pop("config", None)
        else:
            sys.modules["config"] = previous


def semantic_output(documents, *, blind=False, reference=None, isolated=False):
    """A valid wire fixture only; no programmatic semantic certification claim."""
    evidence = [{"doc_id": documents[0]["doc_id"], "field": "content", "location_scope": "field",
                 "role": "support", "explanation": "Offline scripted evidence relation."}]
    value = {"interpretation": "Complete the public question.", "answerability": "answerable",
        "major_requirements": ["Complete the public question."],
        "coverage": {"status": "complete", "scope_conflict": False,
            "inspected_doc_ids": [d["doc_id"] for d in documents], "limitations": []},
        "evidence": evidence, "reasoning": "Offline fixture; not a claim of semantic accuracy."}
    if blind:
        return {**value, "answer": "The report was published on 2024-03-20."}
    value.update(item_validity="valid", reference_status="supported",
        reviewed_answer="The report was published on 2024-03-20.",
        reviewed_rationale="PRIVATE_REVIEW_SENTINEL", concerns=[],
        original_answer_review=[{"requirement": "Complete the public question.",
            "assessment": "完成", "explanation": "Offline scripted decision."}],
        original_rationale_review={"status": "reviewed", "claims": [{
            "claim": "Original reasoning", "assessment": "成立", "explanation": "Offline scripted decision."}],
            "limitations": []},
        review_findings={"substantive_defects": [], "acceptable_brevity": [], "editorial_suggestions": []})
    if isolated:
        value["reference_audit_response"] = "Offline fixture responds to the attributed reference audit."
        value["original_answer_review"][0].update(target_scope="quoted_text", reference_targets=[{
            "reference_part": "answer", "source_quote": first_text(reference["answer"])}])
        value["original_rationale_review"]["claims"][0].update(target_scope="quoted_text", reference_targets=[{
            "reference_part": "rationale", "source_quote": first_text(reference["rationale"])}])
    return value


def reference_report(questions, corpus, protocol, *, max_calls=None, isolated=False):
    documents, _ = visible_documents(corpus)
    def call(step, messages, **params):
        payload = json.loads(messages[1]["content"])
        if step.endswith("blind_read"):
            return semantic_output(documents, blind=True)
        if "original_reference" in payload:
            original = payload["original_reference"]
            return {"decision": "accept", "reason": "Offline fixture only.",
                "task_requirements": ["Complete the public question."], "task_coverage": "Offline fixture only.",
                "claims": [{"reference_part": "answer", "source_quote": first_text(original["answer"]),
                    "assessment": "成立", "explanation": "Offline fixture only.",
                    "evidence": semantic_output(documents, blind=True)["evidence"]}],
                "substantive_defects": [], "acceptable_brevity": [], "editorial_suggestions": [],
                "suggested_revision": "", "limitations": [],
                "evidence": semantic_output(documents, blind=True)["evidence"]}
        original = payload.get("reference_proposal")
        return semantic_output(documents, reference=original, isolated=isolated)
    kwargs = {"reference_auditor_model": "fixture-auditor"} if isolated else {}
    return review_questions(questions, corpus, protocol, reviewer_model="fixture-reviewer",
        reader_model="fixture-reader", chat_json=call, max_calls=max_calls, **kwargs)


def grade_output(documents, *, dispute=False):
    value = semantic_output(documents)
    value.update(answer_verdict="uncertain" if dispute else "correct", format_compliance="compliant",
        additional_facts={"status": "not_assessed", "reason": "Offline fixture."},
        answer_review={"primary_task": "Complete the public question.",
            "answer_meaning": "The scripted answer is a complete natural-language answer.",
            "claims": [{"claim": "Scripted answer claim", "task_role": "primary", "assessment": "supported",
                        "evidence_indices": [0], "explanation": "Offline fixture."}],
            "reference_comparison": "Offline fixture; not an accuracy assertion."},
        requires_item_reassessment=dispute,
        reassessment_reason="Scripted same-item reference dispute." if dispute else "No scripted dispute.")
    if dispute:
        value["reference_status"] = "contradicted"
    return value


class AgentEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sequence = 0
        self.network = patch("socket.socket", side_effect=AssertionError("network disabled"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.make_case()

    def make_case(self, *, n=2, review_calls=None, original=False):
        self.sequence += 1
        self.case = self.root / str(self.sequence)
        self.case.mkdir()
        if original:
            source = ROOT / "output/implementation_bc_agent_loop_20260917/prospective_case_independent"
            if not (source / "question_packages.json").is_file():
                self.skipTest("Original four-case local artifact is unavailable")
            material = cli.read(source / "public_material.json")
            package = cli.read(source / "question_packages.json")
            self.questions = [{"qid": q["item_id"], "question": q["question"],
                "reference_proposal": deepcopy(q["reference_proposal"])} for q in package["questions"]]
            self.corpus, self.protocol = material["documents"], material["public_protocol"]
        else:
            self.questions = [{"qid": f"q{i+1}", "question": f"报告发布日期是什么？（问题{i+1}）",
                "reference_proposal": {"answer": "2024-03-20", "rationale": "PRIVATE_REFERENCE_RATIONALE"},
                "seed": "PRIVATE_SEED"} for i in range(n)]
            self.corpus, self.protocol = deepcopy(CORPUS), PROTOCOL
        self.snap = snapshot(self.questions, self.corpus, self.protocol)
        self.review = reference_report(self.snap["questions"], self.corpus, self.protocol,
                                       max_calls=review_calls, isolated=original)
        for name, value in (("snapshot.json", self.snap), ("review.json", self.review),
                            ("calibration.json", {"offline_fixture": True, "files": {}})):
            (self.case / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        self.plan = {"version": cli.VERSION, "result_scope": "research_only",
            "systems": [{"name": "A", "model": "solver-a", "max_tokens": 128},
                        {"name": "B", "model": "solver-b", "max_tokens": 256}],
            "judge": {"model": "grader", "max_tokens": 384}, "transport": transport(),
            "max_provider_attempts": 4 * len(self.questions), "keep_easy_ratio": 0.0, "filter_seed": 17,
            "full_context_char_budget": 200000, "max_input_chars": 200000,
            "source_sha256": cli.source_hashes(), "runtime": cli.runtime_identity(),
            "input_sha256": {name: cli.sha(self.case / name) for name in cli.INPUTS}}
        self.write_plan()

    def write_plan(self):
        (self.case / "plan.json").write_text(json.dumps(self.plan, ensure_ascii=False), encoding="utf-8")

    def execute(self, *, answer_value="  The report was published on 2024-03-20.\nFull explanation.  ",
                dispute_grade=None, fail_at=None, malformed_grade=False, after_call=None, provider_override=None,
                grade_transform=None):
        documents, _ = visible_documents(self.corpus)
        self.grades = 0
        def create(**wire):
            position = len(self.api.calls)
            if fail_at == position:
                raise TimeoutError("Offline provider failure.")
            if wire["model"] == "grader":
                self.grades += 1
                raw = {} if malformed_grade else grade_output(documents, dispute=self.grades == dispute_grade)
                if grade_transform:
                    raw = grade_transform(raw)
            else:
                raw = {"answer": answer_value}
            if after_call:
                after_call(position, wire)
            return response(json.dumps(raw, ensure_ascii=False))
        self.api = FakeSDK(create=create)
        self.config = load_config(self.api)
        self.config.MODEL = "CONFIG_MODEL_MUST_NOT_WIN"
        self.config.MIN_COMPLETION_TOKENS = 99999
        with ExitStack() as stack:
            stack.enter_context(configured_module(self.config))
            stack.enter_context(patch.object(cli, "check_calibration", return_value=cli.read(self.case / "calibration.json")))
            if provider_override:
                stack.enter_context(patch.object(cli, "provider", side_effect=provider_override))
            for name in ("load", "load_predictions", "append", "append_prediction"):
                stack.enter_context(patch.object(qa_cache, name, side_effect=AssertionError("QA cache forbidden")))
            stack.enter_context(redirect_stdout(io.StringIO()))
            result = cli.run(self.case, execute=True)
        self.report = cli.read(self.case / "execution/report.json")
        return result

    def assert_denominator(self):
        expected = [q["qid"] for q in self.snap["questions"]]
        for system in self.plan["systems"]:
            rows = self.report["results"][system["name"]]["records"]
            self.assertEqual([row["qid"] for row in rows], expected)

    def calibration_fixture(self, *, mutate=None, drift=None):
        """Write conspicuously synthetic gate inputs with real SemanticJudge rows."""
        self.sequence += 1
        folder = self.root / ("offline-calibration-" + str(self.sequence))
        folder.mkdir()
        documents, _ = visible_documents(self.corpus)
        outputs = [grade_output(documents), grade_output(documents)]
        outputs[1]["answer_verdict"] = "incorrect"
        outputs[1]["answer_review"]["claims"][0]["assessment"] = "contradicted"
        grader = cli.SemanticJudge(self.review, self.snap["questions"], self.corpus, self.protocol,
            model="grader", max_tokens=384, max_calls=2,
            chat_json=lambda *a, **kw: outputs.pop(0))
        question = self.snap["questions"][0]
        rows = []
        for name, pred, verdict in (("positive", "2024-03-20", "correct"),
                                    ("negative", "2024-03-21", "incorrect")):
            judgement = grader(question, pred)
            row = {**deepcopy(question), "name": name, "pred": pred, "expected_verdict": verdict,
                   "judgement": judgement, "correct": judgement["correct"]}
            self.assertTrue(is_scored(row))
            rows.append(row)
        calibrated_transport = transport()
        calibrated_transport["profiles"] = {"g": calibrated_transport["profiles"]["g"]}
        calibrated_transport["model_profiles"] = {"grader": "g"}
        profile = resolve_profile(calibrated_transport, "grader")
        wire_parameters = build_request_parameters(model="grader", max_tokens=384,
            temperature=0.0, top_p=1.0, transport=calibrated_transport)
        wire_parameters["deadline_s"] = profile["profile"]["deadline_seconds"]
        actual_wire = [{"model": "grader", "parameters": deepcopy(wire_parameters),
            "transport": deepcopy(profile), "sdk_options": {"max_retries": 0,
                "timeout_seconds": profile["profile"]["http_timeout_seconds"]}} for _ in rows]
        source_names = set(cli.CALIBRATION_SOURCES) | {
            "pipeline/quality_workflow.py", "eval/public_context.py", "eval/qa_cache.py"}
        payloads = {
            "results": {"offline_fixture": "SYNTHETIC; NOT AN ACTUAL MODEL CALIBRATION",
                "fatal_errors": [], "cached_results_loaded": 0,
                "final_source_freeze_verification": {"status": "matches",
                    "source_sha256": {name: cli.sha(ROOT / name) for name in source_names}},
                "outcomes": [{"control": "offline-date-control", "review_eligible": True, "records": rows}],
                "ledger": {"all_observed_wires_match": True, "call_error_events": 0,
                    "json_error_events": 0, "request_attempt_events": 2, "response_events": 2,
                    "actual_wire": actual_wire}},
            "plan": {"offline_fixture": True, "model": "grader", "stage_limits": {"max_tokens": 384},
                     "transport": calibrated_transport},
            "semantic_audit": {"version": "completed-calibration-independent-audit/v1",
                "offline_fixture": "SCRIPTED AUDIT; NOT HUMAN OR MODEL SEMANTIC ACCEPTANCE",
                "prediction_audits": [{"control": "offline-date-control", "prediction_name": row["name"],
                    "actual_verdict": row["judgement"]["verdict"], "fixed_prediction": row["pred"],
                    "independent_semantic_acceptance": True} for row in rows]}}
        observations = [{"control": "offline-date-control", "name": row["name"],
                         "verdict": row["judgement"]["verdict"]} for row in rows]
        if mutate:
            mutate(payloads)
        files = {}
        for name, value in payloads.items():
            path = folder / (name + ".json")
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            files[name] = {"path": str(path), "sha256": cli.sha(path)}
        if drift:
            with Path(files[drift]["path"]).open("a", encoding="utf-8") as stream:
                stream.write("\n")
        return {"version": "agent-calibration-evidence/v1", "files": files,
                "reviewed_observations": observations}

    def test_dry_run_no_config_sdk_provider_or_output(self):
        before = {p.name: p.read_bytes() for p in self.case.iterdir()}
        with patch.object(cli, "check_calibration", return_value={"offline_fixture": True, "files": {}}), \
                patch.object(cli, "provider", side_effect=AssertionError("provider initialized")):
            result = cli.run(self.case)
        self.assertEqual(result["calls_permitted"], 0)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.case.iterdir()})
        code = """import builtins,json,sys
original=builtins.__import__
def guarded(name,*args,**kwargs):
    if name.split('.')[0] in {'config','openai','dotenv'}:
        raise AssertionError('Provider import during dry run: '+name)
    return original(name,*args,**kwargs)
builtins.__import__=guarded
from tools import run_agent_evaluation as cli
cli.check_calibration=lambda *a,**k:{'offline_fixture':True,'files':{}}
cli.provider=lambda *a,**k:(_ for _ in ()).throw(AssertionError('provider initialized'))
print(json.dumps(cli.run(sys.argv[1])))
assert 'config' not in sys.modules
"""
        completed = subprocess.run([sys.executable, "-B", "-c", code, str(self.case)], cwd=ROOT,
                                   capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["calls_permitted"], 0)

    def test_preflight_rejects_input_source_runtime_profile_drift_without_claim(self):
        original = deepcopy(self.plan)
        mutations = (lambda p: p["input_sha256"].update({"snapshot.json": "0" * 64}),
            lambda p: p["source_sha256"].update({cli.SOURCES[0]: "0" * 64}),
            lambda p: p["runtime"].update(executable="wrong-interpreter"),
            lambda p: p["transport"]["model_profiles"].update({"solver-a": "legacy"}))
        for mutation in mutations:
            self.plan = deepcopy(original); mutation(self.plan); self.write_plan()
            with self.subTest(mutation=mutation), patch.object(cli, "check_calibration", return_value={}), \
                    patch.object(cli, "provider") as provider, self.assertRaises(ValueError):
                cli.run(self.case, execute=True)
            provider.assert_not_called()
            self.assertFalse((self.case / "execution").exists())

    def test_forged_calibration_passed_flag_rejected_before_claim(self):
        (self.case / "calibration.json").write_text('{"status":"passed"}', encoding="utf-8")
        self.plan["input_sha256"]["calibration.json"] = cli.sha(self.case / "calibration.json")
        self.write_plan()
        with patch.object(cli, "provider") as provider, self.assertRaises(ValueError):
            cli.run(self.case, execute=True)
        provider.assert_not_called()
        self.assertFalse((self.case / "execution").exists())

    def test_real_calibration_gate_accepts_matching_artifacts_and_added_solver_profiles(self):
        evidence = self.calibration_fixture()
        with patch.object(cli, "provider", side_effect=AssertionError("gate must not call provider")):
            checked = cli.check_calibration(evidence, self.plan["judge"], self.plan["transport"])
        self.assertEqual(checked["observations"], evidence["reviewed_observations"])
        self.assertEqual(checked["files"], evidence["files"])
        (self.case / "calibration.json").write_text(json.dumps(evidence), encoding="utf-8")
        self.plan["input_sha256"]["calibration.json"] = cli.sha(self.case / "calibration.json")
        self.write_plan()
        with patch.object(cli, "provider", side_effect=AssertionError("dry run provider")):
            prepared = cli.run(self.case)
        self.assertTrue(prepared["ready"])
        self.assertFalse((self.case / "execution").exists())

    def test_real_calibration_gate_rejects_changed_model_tokens_profile_sources_and_audit(self):
        mutations = {
            "plan model": lambda p: p["plan"].update(model="different-grader"),
            "plan tokens": lambda p: p["plan"]["stage_limits"].update(max_tokens=385),
            "judge profile": lambda p: p["plan"]["transport"]["profiles"]["g"].update(reasoning_effort="low"),
            "missing source helper": lambda p: p["results"]["final_source_freeze_verification"]["source_sha256"].pop("pipeline/reference_locations.py"),
            "source hash": lambda p: p["results"]["final_source_freeze_verification"]["source_sha256"].update({"eval/semantic_judge.py": "0" * 64}),
            "unexpected actual verdict": lambda p: p["results"]["outcomes"][0]["records"][0].update(expected_verdict="incorrect"),
            "audit label": lambda p: p["semantic_audit"]["prediction_audits"][0].update(actual_verdict="incorrect"),
            "audit prediction": lambda p: p["semantic_audit"]["prediction_audits"][0].update(fixed_prediction="different prediction"),
            "audit rejects": lambda p: p["semantic_audit"]["prediction_audits"][0].update(independent_semantic_acceptance=False),
            "incomplete ledger": lambda p: p["results"]["ledger"].update(response_events=1),
            "wire mismatch": lambda p: p["results"]["ledger"].update(all_observed_wires_match=False),
            "cached run": lambda p: p["results"].update(cached_results_loaded=1),
        }
        for label, mutation in mutations.items():
            evidence = self.calibration_fixture(mutate=mutation)
            with self.subTest(label=label), self.assertRaises(ValueError):
                cli.check_calibration(evidence, self.plan["judge"], self.plan["transport"])
        for file in ("results", "plan", "semantic_audit"):
            evidence = self.calibration_fixture(drift=file)
            with self.subTest(drift=file), self.assertRaises(ValueError):
                cli.check_calibration(evidence, self.plan["judge"], self.plan["transport"])

    def test_calibration_declared_model_cannot_override_actual_grading_model(self):
        def wrong_actual_model(payloads):
            payloads["results"]["outcomes"][0]["records"][0]["judgement"]["judge_model"] = "different-actual-grader"
        evidence = self.calibration_fixture(mutate=wrong_actual_model)
        with self.assertRaises(ValueError):
            cli.check_calibration(evidence, self.plan["judge"], self.plan["transport"])

    def test_calibration_checks_each_actual_wire_not_only_match_flag(self):
        mutations = {
            "wire model": lambda wire: wire.update(model="wrong-wire-model"),
            "wire token budget": lambda wire: wire["parameters"].update(max_completion_tokens=385),
            "wire reasoning": lambda wire: wire["parameters"].update(reasoning_effort="low"),
            "wire transport fingerprint": lambda wire: wire["transport"].update(fingerprint="0" * 64),
            "wire transport profile": lambda wire: wire["transport"]["profile"].update(reasoning_effort="low"),
            "SDK retry": lambda wire: wire["sdk_options"].update(max_retries=1),
            "SDK timeout": lambda wire: wire["sdk_options"].update(timeout_seconds=6),
        }
        for label, mutation in mutations.items():
            def mutate(payloads):
                mutation(payloads["results"]["ledger"]["actual_wire"][0])
            evidence = self.calibration_fixture(mutate=mutate)
            with self.subTest(label=label), self.assertRaises(ValueError):
                cli.check_calibration(evidence, self.plan["judge"], self.plan["transport"])
        for omit_all in (False, True):
            def missing_wire(payloads):
                if omit_all:
                    payloads["results"]["ledger"].pop("actual_wire")
                else:
                    payloads["results"]["ledger"]["actual_wire"].pop()
            evidence = self.calibration_fixture(mutate=missing_wire)
            with self.subTest(omit_all=omit_all), self.assertRaises(ValueError):
                cli.check_calibration(evidence, self.plan["judge"], self.plan["transport"])

    def test_truncated_fullcontext_rejected_during_dry_run(self):
        self.plan["full_context_char_budget"] = 1
        self.write_plan()
        with patch.object(cli, "check_calibration", return_value={}), \
                patch.object(cli, "provider") as provider, self.assertRaises(ValueError):
            cli.run(self.case)
        provider.assert_not_called()
        self.assertFalse((self.case / "execution").exists())

    def test_missing_explicit_profile_timeouts_rejected_before_dispatch(self):
        for field in ("http_timeout_seconds", "deadline_seconds"):
            original = deepcopy(self.plan)
            self.plan["transport"]["profiles"]["a"].pop(field)
            self.write_plan()
            with self.subTest(field=field), patch.object(cli, "check_calibration", return_value={}), \
                    patch.object(cli, "provider") as provider, self.assertRaises(ValueError):
                cli.run(self.case, execute=True)
            provider.assert_not_called()
            self.assertFalse((self.case / "execution").exists())
            self.plan = original

    def test_real_wire_full_answers_no_private_leak_no_cache_and_empty_filter(self):
        result = self.execute()
        self.assertEqual(result["execution"]["status"], "completed")
        self.assertEqual(len(self.api.calls), 8)
        self.assertEqual(result["actual_provider_attempts"], 8)
        self.assertTrue(self.report["ledger"]["complete_response_audit"])
        self.assert_denominator()
        self.assertEqual(self.report["filter"]["counts"]["all_correct"], 2)
        self.assertEqual(self.report["kept_questions"], [])
        for call in self.api.calls:
            wire, options = call["kwargs"], call["options"]
            model = wire["model"]
            self.assertEqual(options["max_retries"], 0)
            self.assertEqual(wire["response_format"], {"type": "json_object"})
            if model == "solver-a":
                self.assertEqual(wire["max_tokens"], 128)
                self.assertEqual(options["timeout"], 3)
            elif model == "solver-b":
                self.assertEqual(wire["max_completion_tokens"], 256)
                self.assertEqual(wire["reasoning_effort"], "medium")
                self.assertNotIn("temperature", wire)
                self.assertNotIn("top_p", wire)
                self.assertEqual(options["timeout"], 4)
            else:
                self.assertEqual(model, "grader")
                self.assertEqual(wire["max_completion_tokens"], 384)
                self.assertEqual(wire["reasoning_effort"], "high")
                self.assertEqual(options["timeout"], 5)
            if model != "grader":
                self.assertNotIn("PRIVATE_", json.dumps(wire["messages"]))
                payload = json.loads(wire["messages"][1]["content"])
                self.assertEqual(set(payload), {"public_question", "public_material", "public_protocol"})
                self.assertEqual(payload["public_protocol"], self.protocol)
        for system in self.plan["systems"]:
            for row in self.report["results"][system["name"]]["records"]:
                self.assertTrue(row["pred"].startswith("  "))
                self.assertTrue(row["pred"].endswith("  "))
                self.assertEqual(row["solver_identity"]["model"], system["model"])
                self.assertEqual(row["solver_identity"]["transport_fingerprint"],
                                 transport_fingerprint(self.plan["transport"]))
        trace = (self.case / "execution/attempts.jsonl").read_text(encoding="utf-8")
        self.assertEqual(sum(json.loads(line)["event"] == "request" for line in trace.splitlines()), 8)
        self.assertIn("Full explanation", trace)

    def test_original_four_structured_references_survive_v8_and_real_run(self):
        self.make_case(original=True)
        before = deepcopy(self.snap["questions"])
        self.assertEqual(len(before), 4)
        self.assertTrue(all(isinstance(q["reference_proposal"]["answer"], dict) for q in before))
        self.assertTrue(all(item["review_state"] == "completed" for item in self.review["items"]),
                        [(i["review_state"], i.get("execution")) for i in self.review["items"]])
        result = self.execute()
        self.assertEqual(result["execution"]["status"], "completed")
        self.assertEqual(result["actual_provider_attempts"], 16)
        self.assert_denominator()
        self.assertEqual(cli.read(self.case / "execution/inputs/snapshot.json")["questions"], before)
        for row in self.report["results"]["A"]["records"]:
            self.assertEqual(row["reference_proposal"], next(q for q in before if q["qid"] == row["qid"])["reference_proposal"])

    def test_pending_reviews_make_no_calls_and_preserve_every_original_row(self):
        self.make_case(n=4, review_calls=0)
        result = self.execute()
        self.assertEqual(result["actual_provider_attempts"], 0)
        self.assertEqual(self.api.calls, [])
        self.assert_denominator()
        self.assertEqual(len(self.report["kept_questions"]), 4)
        for result in self.report["results"].values():
            self.assertTrue(all(row["skip_reason"] == "reference_review_pending" for row in result["records"]))

    def test_mixed_eligible_pending_preserves_order_and_does_not_grade_pending(self):
        self.make_case(review_calls=2)
        self.execute()
        self.assertEqual(len(self.api.calls), 4)
        self.assert_denominator()
        self.assertEqual([q["qid"] for q in self.report["kept_questions"]], ["q2"])

    def test_second_system_dispute_closes_first_correct_before_filter(self):
        self.execute(dispute_grade=2)
        self.assertEqual(len(self.api.calls), 8)
        self.assertEqual(self.report["reassessment_closure"]["n_disputed_items"], 1)
        first = self.report["results"]["A"]["records"][0]
        self.assertTrue(first["original_judgement"]["correct"])
        self.assertTrue(first["quarantined"])
        self.assertFalse(is_scored(first))
        self.assertEqual([q["qid"] for q in self.report["kept_questions"]], ["q1"])
        self.assertEqual(self.report["filter"]["counts"]["all_correct"], 1)

    def test_invalid_answer_envelope_latches_all_later_work(self):
        for value in (" \n", None, {"nested": "answer"}, 7):
            self.make_case()
            with self.subTest(value=value):
                result = self.execute(answer_value=value)
                self.assertEqual(result["execution"]["status"], "failed")
                self.assertEqual(len(self.api.calls), 1)
                self.assertEqual(self.grades, 0)
                self.assert_denominator()
                self.assertTrue(self.report["fatal_errors"])

    def test_provider_failure_has_one_physical_attempt_no_retry(self):
        result = self.execute(fail_at=1)
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertEqual(len(self.api.calls), 1)
        self.assertEqual(result["actual_provider_attempts"], 1)
        self.assert_denominator()

    def test_malformed_grade_latches_after_exactly_answer_and_grade(self):
        result = self.execute(malformed_grade=True)
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertEqual(len(self.api.calls), 2)
        self.assert_denominator()
        self.assertTrue(all(not is_scored(row) for value in self.report["results"].values() for row in value["records"]))

    def test_fixed_attempt_budget_never_reallocates_or_drops_rows(self):
        for cap in (0, 1, 3):
            self.make_case()
            self.plan["max_provider_attempts"] = cap
            self.write_plan()
            with self.subTest(cap=cap):
                result = self.execute()
                self.assertEqual(result["execution"]["status"], "failed")
                self.assertEqual(len(self.api.calls), cap)
                self.assertEqual(result["actual_provider_attempts"], cap)
                self.assert_denominator()

    def test_reservation_write_failure_prevents_physical_dispatch(self):
        original = cli.save
        def failing_save(path, value):
            if Path(path).parent.name == "reservations":
                raise OSError("Offline reservation persistence failure")
            return original(path, value)
        with patch.object(cli, "save", side_effect=failing_save):
            result = self.execute()
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertEqual(len(self.api.calls), 0)
        self.assertEqual(result["actual_provider_attempts"], 0)
        self.assert_denominator()

    def test_grading_audit_failure_stops_before_grader_dispatch(self):
        real_provider = cli.provider
        def failing_audit_provider(*args, **kwargs):
            call, record, clean = real_provider(*args, **kwargs)
            def record_failure(event):
                raise OSError("Offline audit persistence failure")
            return call, record_failure, clean
        result = self.execute(provider_override=failing_audit_provider)
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertEqual(len(self.api.calls), 1)
        self.assertEqual(self.grades, 0)
        self.assert_denominator()

    def test_linked_calibration_drift_after_answer_blocks_grader(self):
        linked = self.case / "external_calibration_result.json"
        linked.write_text('{"offline_fixture":true}', encoding="utf-8")
        calibration = {"offline_fixture": True, "files": {
            "results": {"path": str(linked), "sha256": cli.sha(linked)}}}
        (self.case / "calibration.json").write_text(json.dumps(calibration), encoding="utf-8")
        self.plan["input_sha256"]["calibration.json"] = cli.sha(self.case / "calibration.json")
        self.write_plan()
        def mutate(position, wire):
            if position == 1:
                linked.write_text('{"offline_fixture":"changed"}', encoding="utf-8")
        result = self.execute(after_call=mutate)
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertEqual(len(self.api.calls), 1)
        self.assertEqual(self.grades, 0)
        self.assert_denominator()

    def test_wire_ledger_mismatch_cannot_leave_success_or_filtered_output(self):
        def mutate(position, wire):
            if position == 8:
                path = self.case / "execution/attempts.jsonl"
                lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                request = next(row for row in lines if row.get("event") == "request")
                request["model"] = "undeclared-wire-model"
                path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in lines), encoding="utf-8")
        result = self.execute(after_call=mutate)
        self.assertEqual(len(self.api.calls), 8)
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertFalse(self.report["ledger"]["all_observed_wires_match"])
        self.assertNotIn("filter", self.report)
        self.assertNotIn("kept_questions", self.report)

    def test_malformed_trace_keeps_a_failed_report_and_all_rows(self):
        def corrupt(position, wire):
            if position == 8:
                with (self.case / "execution/attempts.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write("{malformed trace line}\n")
        result = self.execute(after_call=corrupt)
        self.assertEqual(len(self.api.calls), 8)
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertNotIn("filter", self.report)
        self.assertNotIn("kept_questions", self.report)
        self.assertEqual(self.report["ledger"]["error_type"], "JSONDecodeError")
        self.assertIsNone(result["actual_provider_attempts"])
        self.assertTrue(self.report["post_run_inputs_match"])
        self.assertTrue(self.report["post_run_source_matches"])
        self.assert_denominator()

    def test_missing_duplicate_or_orphan_response_audit_with_valid_json_fails(self):
        for defect in ("missing", "duplicate", "orphan"):
            self.make_case()
            def corrupt(position, wire):
                if position != 8:
                    return
                path = self.case / "execution/attempts.jsonl"
                events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                responses = [event for event in events if event.get("event") == "response"]
                self.assertEqual(len(responses), 7)
                if defect == "missing":
                    events = [event for event in events if event.get("event") != "response"]
                elif defect == "duplicate":
                    events.append(deepcopy(responses[0]))
                else:
                    responses[0]["call_id"] = "orphan-response-with-no-request"
                path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
                                encoding="utf-8")
            with self.subTest(defect=defect):
                result = self.execute(after_call=corrupt)
                self.assertEqual(len(self.api.calls), 8)
                self.assertEqual(result["actual_provider_attempts"], 8)
                self.assertEqual(result["execution"]["status"], "failed")
                self.assertTrue(self.report["ledger"]["all_observed_wires_match"])
                self.assertFalse(self.report["ledger"]["complete_response_audit"])
                self.assertNotIn("filter", self.report)
                self.assertNotIn("kept_questions", self.report)
                self.assert_denominator()

    def test_provider_initialization_failure_still_preserves_denominator(self):
        def fail_provider(*args, **kwargs):
            raise RuntimeError("Offline provider initialization failure")
        result = self.execute(provider_override=fail_provider)
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertEqual(result["actual_provider_attempts"], 0)
        self.assert_denominator()

    def test_frozen_input_drift_after_first_answer_stops_before_grader(self):
        def mutate(position, wire):
            if position == 1:
                with (self.case / "snapshot.json").open("a", encoding="utf-8") as stream:
                    stream.write("\n")
        result = self.execute(after_call=mutate)
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertEqual(len(self.api.calls), 1)
        self.assert_denominator()

    @staticmethod
    def nested_reassessment(raw):
        for key in ("requires_item_reassessment", "reassessment_reason"):
            raw["answer_review"][key] = raw.pop(key)
        return raw

    def test_nested_control_pair_requires_explicit_layout_policy(self):
        result = self.execute(grade_transform=self.nested_reassessment)
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertEqual(len(self.api.calls), 2)
        self.assertNotIn("filter", self.report)
        self.assert_denominator()

    def test_explicit_layout_keeps_raw_and_runs_normal_grader_and_filter(self):
        self.plan["grading_response_layout"] = cli.LAYOUT_VERSION
        self.write_plan()
        result = self.execute(grade_transform=self.nested_reassessment)
        self.assertEqual(result["execution"]["status"], "completed")
        self.assertEqual(self.report["filter"]["counts"]["removed_easy"], 2)
        events = [json.loads(line) for line in (self.case / "execution/calls.jsonl").read_text(encoding="utf-8").splitlines()]
        layout = [e for e in events if e.get("event") == "grading_response_layout"]
        self.assertEqual(len(layout), 4)
        for event in layout:
            self.assertNotIn("requires_item_reassessment", event["raw_output"])
            self.assertIs(event["normalized_output"]["requires_item_reassessment"], False)
            self.assertEqual(event["raw_output"]["answer_review"], event["normalized_output"]["answer_review"])
            self.assertEqual(len(event["normalization"]["actions"]), 2)

    def test_nested_true_still_closes_both_system_scores(self):
        self.plan["grading_response_layout"] = cli.LAYOUT_VERSION
        self.write_plan()
        self.execute(grade_transform=self.nested_reassessment, dispute_grade=2)
        for system in self.report["results"].values():
            self.assertFalse(is_scored(system["records"][0]))
        self.assertEqual(self.report["filter"]["counts"]["kept_incomplete"], 1)

    def test_conflicting_control_locations_stop_without_filter(self):
        self.plan["grading_response_layout"] = cli.LAYOUT_VERSION
        self.write_plan()
        def conflicting(raw):
            raw["answer_review"].update(requires_item_reassessment=True, reassessment_reason="Conflicting explicit decision.")
            return raw
        result = self.execute(grade_transform=conflicting)
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertEqual(len(self.api.calls), 2)
        self.assertNotIn("filter", self.report)
        self.assert_denominator()

    def test_unknown_layout_policy_stops_preflight(self):
        self.plan["grading_response_layout"] = "infer-from-reason"
        self.write_plan()
        with self.assertRaisesRegex(ValueError, "layout policy"):
            cli.run(self.case)

    def test_layout_audit_write_failure_latches_after_grader_response(self):
        self.plan["grading_response_layout"] = cli.LAYOUT_VERSION
        self.write_plan()
        real_provider = cli.provider

        def failing_layout_audit_provider(*args, **kwargs):
            call, record, clean = real_provider(*args, **kwargs)

            def record_with_layout_failure(event):
                if event.get("event") == "grading_response_layout":
                    raise OSError("Offline layout audit persistence failure")
                return record(event)

            return call, record_with_layout_failure, clean

        result = self.execute(grade_transform=self.nested_reassessment,
                              provider_override=failing_layout_audit_provider)
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertEqual(len(self.api.calls), 2)
        self.assertEqual(self.grades, 1)
        self.assertEqual(result["actual_provider_attempts"], 2)
        self.assertTrue(self.report["ledger"]["complete_response_audit"])
        self.assertNotIn("filter", self.report)
        self.assertTrue(any(error["stage"] == "audit" for error in self.report["fatal_errors"]))
        self.assert_denominator()


if __name__ == "__main__":
    unittest.main(verbosity=2)
