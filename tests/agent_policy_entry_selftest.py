"""Offline policy-A entry checks; scripted fixtures do not certify semantics.

No external provider is available. Calibration evidence below is explicitly
synthetic and exercises the acceptance gate, not a real calibration result.
"""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import agent_evaluation_selftest as fixtures
import agent_pipeline_cli_selftest as entry_fixtures
import answer_task_review_selftest as reader_fixtures
from eval import answer_task_review as policy
from eval.grading import DIRECT_METHOD, is_scored
from eval.semantic_judge import resolve_scoring_config
from pipeline.quality_workflow import snapshot
from tools import agent_pipeline as entry
from tools import run_agent_evaluation as cli

READER_STEP = "agent_editing.independent_answer_task_review"


def reader_output(answer):
    value = reader_fixtures.opinion()
    value["observations"][0]["source_quote"] = answer
    return value


def final_output(documents, verdict="correct"):
    value = fixtures.grade_output(documents)
    value["answer_verdict"] = verdict
    value["answer_task_review_response"] = "Offline response to the independent reader; not a semantic certification."
    if verdict == "incorrect":
        value["answer_review"]["claims"][0]["assessment"] = "contradicted"
    return value


class PolicyEvaluationTests(unittest.TestCase):
    setUp = fixtures.AgentEvaluationTests.setUp
    make_case = fixtures.AgentEvaluationTests.make_case
    write_plan = fixtures.AgentEvaluationTests.write_plan
    assert_denominator = fixtures.AgentEvaluationTests.assert_denominator
    calibration_fixture = fixtures.AgentEvaluationTests.calibration_fixture

    def policy_case(self, *, n=1, budget=None, method=policy.VERSION, append=True):
        self.make_case(n=n)
        if append:
            self.protocol = policy.append_scoring_policy(self.protocol)
        self.snap = snapshot(self.questions, self.corpus, self.protocol)
        self.review = fixtures.reference_report(self.snap["questions"], self.corpus, self.protocol)
        for name, value in (("snapshot.json", self.snap), ("review.json", self.review)):
            (self.case / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        self.plan.update(version=cli.POLICY_PLAN_VERSION, scoring_policy=policy.POLICY_VERSION,
            grading_method=method, max_provider_attempts=(6 if method == policy.VERSION else 4) * n
            if budget is None else budget,
            input_sha256={name: cli.sha(self.case / name) for name in cli.INPUTS})
        self.write_plan()

    def execute(self, *, reader_bad=False, provider_override=None, input_copy_failure=False):
        documents, _ = fixtures.visible_documents(self.corpus)
        self.payloads = []
        def create(**wire):
            payload = json.loads(wire["messages"][1]["content"])
            self.payloads.append(deepcopy(payload))
            if wire["model"] != "grader":
                value = {"answer": "The report was published on 2024-03-20."}
            elif "reference_review" in payload:
                value = final_output(documents)
            else:
                value = {} if reader_bad else reader_output(payload["solver_answer"])
            return fixtures.response(json.dumps(value, ensure_ascii=False))
        self.api = fixtures.FakeSDK(create=create)
        config = fixtures.load_config(self.api)
        original_open = Path.open
        def guarded_open(path, *args, **kwargs):
            if args and args[0] == "xb" and path.name == "plan.json" and path.parent.name == "inputs":
                raise OSError("Offline input-copy failure")
            return original_open(path, *args, **kwargs)
        with ExitStack() as stack:
            stack.enter_context(fixtures.configured_module(config))
            stack.enter_context(patch.object(cli, "check_calibration", return_value={"files": {}}))
            if provider_override is not None:
                stack.enter_context(patch.object(cli, "provider", side_effect=provider_override))
            if input_copy_failure:
                stack.enter_context(patch.object(Path, "open", guarded_open))
            for name in ("load", "load_predictions", "append", "append_prediction"):
                stack.enter_context(patch.object(fixtures.qa_cache, name,
                    side_effect=AssertionError("QA cache forbidden")))
            stack.enter_context(redirect_stdout(io.StringIO()))
            value = cli.run(self.case, execute=True)
        self.report = cli.read(self.case / "execution/report.json")
        return value

    def synthetic_policy_calibration(self, *, method=policy.VERSION, mutate=None):
        """Real bound grade objects plus conspicuously scripted wire/audit records."""
        scoring = resolve_scoring_config(policy.POLICY_VERSION, method)
        documents, _ = fixtures.visible_documents(self.corpus)
        calls = []
        verdict = ["correct"]
        def invoke(step, messages, **params):
            calls.append(step)
            payload = json.loads(messages[1]["content"])
            return (reader_output(payload["solver_answer"]) if step == READER_STEP
                else final_output(documents, verdict[0]))
        grader = cli.SemanticJudge(self.review, self.snap["questions"], self.corpus, self.protocol,
            model="grader", max_tokens=384, max_calls=4, chat_json=invoke,
            scoring_policy=policy.POLICY_VERSION, grading_method=method)
        rows = []
        for name, pred, expected in (("positive", "2024-03-20", "correct"),
                                     ("negative", "2024-03-21", "incorrect")):
            verdict[0] = expected
            judgement = grader(self.snap["questions"][0], pred)
            row = {**deepcopy(self.snap["questions"][0]), "name": name, "pred": pred,
                "expected_verdict": expected, "judgement": judgement, "correct": judgement["correct"],
                "evaluation_provenance": cli.record_provenance(self.snap["questions"][0], grader.public_context)}
            self.assertTrue(is_scored(row), judgement.get("reason"))
            rows.append(row)
        def convert(payloads):
            result = payloads["results"]
            result["scoring_configuration"] = deepcopy(scoring)
            result["outcomes"][0]["records"] = rows
            result["final_source_freeze_verification"]["source_sha256"].update(
                {name: cli.sha(ROOT / name) for name in cli.POLICY_CALIBRATION_SOURCES})
            payloads["plan"]["scoring_configuration"] = deepcopy(scoring)
            steps = ["semantic_review.blind_read", "agent_editing.independent_reference_audit",
                     "semantic_review.adjudicate", *calls]
            ledger = result["ledger"]
            template = ledger["actual_wire"][0]
            ledger.update(complete_response_audit=True, reserved_dispatches=len(steps),
                request_attempt_events=len(steps), response_events=len(steps),
                actual_wire=[{**deepcopy(template), "step": step} for step in steps])
            if mutate:
                mutate(payloads)
        return self.calibration_fixture(mutate=convert)

    def test_reader_route_two_answers_have_six_exact_roles_and_public_protocol(self):
        self.policy_case()
        self.execute()
        self.assertEqual(self.report["execution"]["status"], "completed")
        self.assertEqual(len(self.api.calls), 6)
        expected = ["solver.A", READER_STEP, "semantic_judge.answer",
                    "solver.B", READER_STEP, "semantic_judge.answer"]
        reservations = [cli.read(p) for p in sorted((self.case / "execution/reservations").glob("*.json"))]
        self.assertEqual([r["step"] for r in reservations], expected)
        self.assertTrue(all(p["public_protocol"] == self.protocol for p in self.payloads))
        for index in (1, 4):
            self.assertNotIn("PRIVATE_", json.dumps(self.payloads[index]))
            self.assertNotIn("reference_review", self.payloads[index])
            self.assertNotIn("independent_answer_task_review", self.payloads[index])
            self.assertIn("independent_answer_task_review", self.payloads[index + 1])
        self.assert_denominator()
        self.assertTrue(all(is_scored(row) for result in self.report["results"].values()
                            for row in result["records"]))
        self.assertTrue(self.report["ledger"]["complete_response_audit"])
        self.assertTrue(self.report["ledger"]["all_observed_wires_match"])

    def test_budget_zero_through_six_never_dispatches_partial_answer_unit(self):
        for budget in range(7):
            with self.subTest(budget=budget):
                self.policy_case(budget=budget)
                self.execute()
                self.assertEqual(len(self.api.calls), (budget // 3) * 3)
                self.assert_denominator()
                rows = [self.report["results"][s]["records"][0] for s in ("A", "B")]
                self.assertEqual(sum(is_scored(r) for r in rows), budget // 3)
                for row in rows[budget // 3:]:
                    self.assertEqual(row["skip_reason"], "provider_budget_exhausted_before_item")
                    self.assertEqual(row["pred"], "")
                    self.assertIsNone(row["correct"])
                self.assertEqual(self.report["fatal_errors"], [])

    def test_direct_policy_uses_one_grade_call_and_two_call_work_units(self):
        for budget in (1, 3, 4):
            with self.subTest(budget=budget):
                self.policy_case(budget=budget, method=DIRECT_METHOD)
                self.execute()
                self.assertEqual(len(self.api.calls), 2 * (budget // 2))
                self.assertTrue(all("reference_review" in p for p, wire in
                    zip(self.payloads, self.api.calls) if wire["kwargs"]["model"] == "grader"))
                self.assert_denominator()

    def test_malformed_reader_stops_before_final_or_other_solver(self):
        self.policy_case()
        self.execute(reader_bad=True)
        self.assertEqual(len(self.api.calls), 2)
        self.assertEqual(self.report["execution"]["status"], "failed")
        self.assert_denominator()
        self.assertFalse(any(is_scored(r) for v in self.report["results"].values() for r in v["records"]))

    def test_missing_policy_in_frozen_protocol_rejected_before_provider(self):
        self.policy_case(append=False)
        with patch.object(cli, "check_calibration", return_value={"files": {}}), \
                patch.object(cli, "provider", side_effect=AssertionError("Provider forbidden")):
            with self.assertRaisesRegex(ValueError, "exact policy block"):
                cli.run(self.case, execute=True)
        self.assertFalse((self.case / "execution").exists())

    def test_old_calibration_cannot_certify_policy(self):
        self.policy_case()
        evidence = self.calibration_fixture()
        with self.assertRaisesRegex(ValueError, "policy, method, prompt or layout"):
            cli.check_calibration(evidence, self.plan["judge"], self.plan["transport"],
                scoring=resolve_scoring_config(policy.POLICY_VERSION, policy.VERSION))

    def test_new_calibration_gate_accepts_exact_reader_and_direct_routes(self):
        for method in (policy.VERSION, DIRECT_METHOD):
            with self.subTest(method=method):
                self.policy_case(method=method)
                evidence = self.synthetic_policy_calibration(method=method)
                accepted = cli.check_calibration(evidence, self.plan["judge"], self.plan["transport"],
                    scoring=resolve_scoring_config(policy.POLICY_VERSION, method))
                self.assertEqual(len(accepted["observations"]), 2)

    def test_new_calibration_rejects_method_layout_source_and_ledger_role_drift(self):
        self.policy_case()
        scoring = resolve_scoring_config(policy.POLICY_VERSION, policy.VERSION)
        mutations = {
            "method": lambda p: p["plan"]["scoring_configuration"].update(grading_method=DIRECT_METHOD),
            "layout": lambda p: p["plan"].update(grading_response_layout=cli.LAYOUT_VERSION),
            "source": lambda p: p["results"]["final_source_freeze_verification"]["source_sha256"].update(
                {"eval/answer_task_review.py": "stale"}),
            "missing_reader": lambda p: [w.update(step="semantic_judge.answer") for w in
                p["results"]["ledger"]["actual_wire"] if w["step"] == READER_STEP],
            "reservation": lambda p: p["results"]["ledger"].update(reserved_dispatches=6),
            "incomplete_response": lambda p: p["results"]["ledger"].update(complete_response_audit=False),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                evidence = self.synthetic_policy_calibration(mutate=mutate)
                with self.assertRaises(ValueError):
                    cli.check_calibration(evidence, self.plan["judge"], self.plan["transport"], scoring=scoring)

    def test_policy_calibration_cannot_be_used_as_legacy(self):
        self.policy_case()
        evidence = self.synthetic_policy_calibration()
        with self.assertRaisesRegex(ValueError, "does not certify legacy"):
            cli.check_calibration(evidence, self.plan["judge"], self.plan["transport"])

    def test_initialization_and_input_copy_failure_preserve_full_denominator(self):
        for failure in ("provider", "input_copy"):
            with self.subTest(failure=failure):
                self.policy_case(n=2)
                self.execute(provider_override=RuntimeError("offline init failure") if failure == "provider" else None,
                    input_copy_failure=failure == "input_copy")
                self.assertEqual(self.report["execution"]["status"], "failed")
                self.assertEqual(self.api.calls, [])
                self.assert_denominator()
                self.assertFalse(any(is_scored(r) for v in self.report["results"].values() for r in v["records"]))

    def test_legacy_budget_behavior_and_calibration_remain_unchanged(self):
        self.make_case(n=1)
        self.plan["max_provider_attempts"] = 3
        self.write_plan()
        fixtures.AgentEvaluationTests.execute(self)
        self.assertEqual(len(self.api.calls), 3)
        self.assertEqual(self.report["execution"]["status"], "failed")
        self.assert_denominator()
        evidence = self.calibration_fixture()
        self.assertEqual(len(cli.check_calibration(evidence, self.plan["judge"], self.plan["transport"])
                             ["observations"]), 2)


class PolicyPreparationTests(unittest.TestCase):
    setUp = entry_fixtures.EntryTests.setUp
    write = entry_fixtures.EntryTests.write
    gen_spec = entry_fixtures.EntryTests.gen_spec
    eval_spec = entry_fixtures.EntryTests.eval_spec

    def test_generation_adds_exact_policy_once_and_preserves_original_bytes(self):
        spec = self.gen_spec()
        value = entry._read(spec)
        value.update(version=entry.POLICY_GENERATION_SPEC, scoring_policy=policy.POLICY_VERSION)
        self.write(spec, value)
        original = (self.config / "protocol.txt").read_bytes()
        case = self.base / "policy generation"
        with patch.object(entry_fixtures.generation, "provider", side_effect=AssertionError("Provider forbidden")):
            result = entry.prepare_generation(spec, case)
        self.assertTrue(result["ready"])
        self.assertEqual((case / "protocol.original.txt").read_bytes(), original)
        protocol = (case / "protocol.txt").read_bytes().decode("utf-8")
        self.assertEqual(protocol, policy.append_scoring_policy(original.decode("utf-8-sig")))
        self.assertEqual(protocol.count(policy.POLICY_TEXT), 1)
        plan = entry._read(case / "plan.json")
        self.assertEqual(plan["preparation"]["public_scoring_policy"], policy.public_scoring_policy())
        self.assertIn("eval/answer_task_review.py", plan["source_sha256"])
        self.assertFalse((case / "execution").exists())

    def test_preparation_rejects_conflicting_policy_and_legacy_version_with_new_fields(self):
        for invalid in ("legacy_with_policy", "unknown_policy", "conflicting_protocol"):
            with self.subTest(invalid=invalid):
                spec = self.gen_spec()
                value = entry._read(spec)
                value.update(version=entry.POLICY_GENERATION_SPEC, scoring_policy=policy.POLICY_VERSION)
                if invalid == "legacy_with_policy":
                    value["version"] = entry.GENERATION_SPEC
                elif invalid == "unknown_policy":
                    value["scoring_policy"] = "invented/v9"
                else:
                    (self.config / "protocol.txt").write_text(policy.append_scoring_policy("public") + " extra",
                        encoding="utf-8")
                self.write(spec, value)
                with self.assertRaises(ValueError):
                    entry.prepare_generation(spec, self.base / invalid)

    def test_evaluation_v2_copies_already_public_protocol_and_forwards_exact_method(self):
        source = self.base / "policy source"
        directory = source / "execution"
        directory.mkdir(parents=True)
        protocol = policy.append_scoring_policy("Only public materials.")
        report = entry_fixtures.run_agent_case({"goal": "fixture"}, {"goal": "fixture"}, {"goal": "fixture"},
            protocol, plan=entry_fixtures.team_plan(), chat_json=entry_fixtures.TeamScript(drop_second=True))
        entry_fixtures.generation._exports(directory, report, deepcopy)
        self.write(directory / "phases/final_review/report.json", report["phases"]["final_review"])
        spec = self.eval_spec(source)
        value = entry._read(spec)
        value["version"] = entry.POLICY_EVALUATION_SPEC
        value["evaluation_plan"].update(scoring_policy=policy.POLICY_VERSION,
            grading_method=policy.VERSION, max_provider_attempts=6)
        self.write(spec, value)
        case = self.base / "policy evaluation"
        with patch.object(cli, "check_calibration", return_value={"files": {}}) as gate, \
                patch.object(cli, "provider", side_effect=AssertionError("Provider forbidden")):
            result = entry.prepare_evaluation(spec, case)
        self.assertTrue(result["ready"])
        self.assertEqual(cli.read(case / "snapshot.json")["protocol"], protocol)
        self.assertEqual((case / "snapshot.json").read_bytes(), (directory / "snapshot.json").read_bytes())
        self.assertEqual(cli.read(case / "plan.json")["version"], cli.POLICY_PLAN_VERSION)
        self.assertEqual(gate.call_args.kwargs["scoring"], resolve_scoring_config(policy.POLICY_VERSION, policy.VERSION))
        self.assertFalse((case / "execution").exists())


if __name__ == "__main__":
    unittest.main()
