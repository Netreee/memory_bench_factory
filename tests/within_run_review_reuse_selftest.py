"""Offline whole-batch reuse tests; scripted opinions are not quality evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline import agent_factory as factory
from pipeline.quality_workflow import validate_review
from tools import run_agent_case as generation
from tools import run_agent_question_cycle as cycle
from tools import agent_pipeline, run_agent_evaluation as evaluation
from agent_factory_selftest import TeamScript, team_plan, plan as legacy_plan, PROTOCOL
from agent_question_cycle_selftest import (failed_upstream, cycle_plan, CycleScript,
    write_upstream_fixture, prepare_fixture_case)
import agent_pipeline_cli_selftest as entry_fixtures


def transport():
    return {"version": "chat-completions-transport/v1", "model_profiles": {}, "default_profile": "explicit",
        "profiles": {"explicit": {"token_limit_parameter": "max_completion_tokens", "reasoning_effort": "high",
            "omit_parameters": ["temperature", "top_p"], "response_format": {"type": "json_object"},
            "http_timeout_seconds": 300, "deadline_seconds": 300}}}


def same_plan(*, enabled=True, cap=27):
    value = cycle_plan(cap)
    value["phases"]["final_review"] = deepcopy(value["phases"]["question_review"])
    value["phases"]["final_collection_review"] = deepcopy(value["phases"]["collection_review"])
    if enabled:
        value["review_reuse"] = factory.REVIEW_REUSE_POLICY
    return value


def context(config):
    return {"transport": transport(), "source_sha256": {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in cycle.source_names(config)}}


class OpinionScript(CycleScript):
    def __init__(self, *, revise=False, negative=False, pending_initial=False):
        super().__init__()
        self.revise, self.negative, self.pending_initial = revise, negative, pending_initial
        self.adjudications = 0

    def __call__(self, step, messages, **params):
        value = super().__call__(step, messages, **params)
        payload = json.loads(messages[1]["content"])
        if params["model"] == "question_edit" and self.revise:
            value["decisions"][0].update(action="revise", reference_proposal=deepcopy(payload["questions"][0]["reference_proposal"]))
            value["decisions"][0]["reference_proposal"]["rationale"] += " OFFLINE changed rationale."
        if step == "semantic_review.adjudicate":
            self.adjudications += 1
            if self.negative:
                value["reference_status"] = "unsupported"
                value["concerns"] = ["OFFLINE negative opinion must not turn into acceptance."]
                value["review_findings"]["substantive_defects"] = ["OFFLINE negative opinion."]
            if self.pending_initial and self.adjudications <= 4:
                value["reference_status"] = "unresolved"
        return value


class WithinRunReuseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = failed_upstream()

    def setUp(self):
        self.net = patch("socket.socket", side_effect=AssertionError("Network forbidden"))
        self.net.start()
        self.addCleanup(self.net.stop)

    def run_cycle(self, *, config=None, script=None, conditions=True, **kwargs):
        config, script = config or same_plan(), script or OpinionScript()
        supplied = context(config) if conditions is True else conditions
        report = factory.run_agent_question_cycle(self.upstream["original_snapshot"], self.upstream,
            plan=config, chat_json=script, review_reuse_context=supplied, **kwargs)
        return report, script

    def test_default_fresh_and_legacy_rejects_policy(self):
        report, script = self.run_cycle(config=same_plan(enabled=False))
        self.assertEqual(len(script.calls), 27)
        self.assertEqual(report["budget"]["phase_provider_attempts"]["final_review"], 12)
        self.assertTrue(report["receipt"]["final_review_is_fresh"])
        self.assertNotIn("within_run_review_reuse", report)
        bad = legacy_plan(); bad["review_reuse"] = factory.REVIEW_REUSE_POLICY
        with self.assertRaises(ValueError): factory._plan(bad)
        for policy in (None, True, "unknown"):
            bad = same_plan(); bad["review_reuse"] = policy
            with self.assertRaises(ValueError):
                factory.prepare_question_cycle(self.upstream["original_snapshot"], self.upstream, bad)

    def test_exact_same_batch_reuses_both_reports_with14_new_calls_and_unchanged_raw(self):
        initial_reports = {}
        def checkpoint(event):
            if event["phase"] in ("question_review", "collection_review"):
                initial_reports[event["phase"]] = deepcopy(event["phase_report"])
        report, script = self.run_cycle(config=same_plan(cap=14), on_phase=checkpoint)
        self.assertEqual(len(script.calls), 14)
        self.assertEqual(report["budget"]["actual_provider_attempts"], 14)
        self.assertEqual(report["upstream_budget"], self.upstream["budget"])
        self.assertFalse(report["receipt"]["final_review_is_fresh"])
        for source, target in (("question_review", "final_review"), ("collection_review", "final_collection_review")):
            actual = deepcopy(report["phases"][target]); note = actual.pop("within_run_reuse")
            self.assertEqual(actual, initial_reports[source])
            self.assertEqual(report["phases"][source], initial_reports[source])
            self.assertEqual(note["new_provider_calls"], 0)
            self.assertEqual(note["historical_calls_used"], 12 if source == "question_review" else 1)
            self.assertEqual(note["reused_from"]["phase"], source)
            self.assertEqual(report["budget"]["phase_provider_attempts"][target], 0)
            self.assertFalse(any(e["phase"] == target for e in report["records"]))
            self.assertFalse(any(e["phase"] == target for e in report["provider_attempts"]))
        validate_review(report["current_snapshot"], report["phases"]["final_review"])
        self.assertEqual(report["receipt"]["counts"]["model_review_eligible"], 4)
        self.assertEqual(report["current_snapshot"]["questions"], report["original_snapshot"]["questions"])

    def test_completed_negative_opinions_reused_without_acceptance(self):
        report, script = self.run_cycle(script=OpinionScript(negative=True))
        self.assertEqual(len(script.calls), 14)
        self.assertEqual(report["receipt"]["counts"]["model_review_eligible"], 0)
        self.assertEqual(report["receipt"]["counts"]["unresolved"], 4)
        self.assertTrue(all(i["reference_status"] == "unsupported" for i in report["phases"]["final_review"]["items"]))
        self.assertFalse(report["receipt"]["scoring_ready"])

    def test_different_collection_conditions_keep_one_fresh_collection_call(self):
        config = same_plan(cap=15)
        config["phases"]["final_collection_review"]["model"] = "final_collection_review"
        report, script = self.run_cycle(config=config)
        self.assertEqual(len(script.calls), 15)
        self.assertEqual(report["budget"]["phase_provider_attempts"]["final_review"], 0)
        self.assertEqual(report["budget"]["phase_provider_attempts"]["final_collection_review"], 1)
        self.assertEqual(report["within_run_review_reuse"]["final_collection_review"]["reason"], "phase_conditions_differ")
        self.assertNotIn("within_run_reuse", report["phases"]["final_collection_review"])

    def test_reference_edit_reviews_entire_batch_and_collection_fresh(self):
        report, script = self.run_cycle(script=OpinionScript(revise=True))
        self.assertEqual(len(script.calls), 27)
        self.assertTrue(report["receipt"]["final_review_is_fresh"])
        self.assertEqual(report["budget"]["phase_provider_attempts"]["final_review"], 12)
        self.assertTrue(all(x["reason"] == "batch_inputs_changed" for x in report["within_run_review_reuse"].values()))
        self.assertNotEqual(report["original_snapshot"]["questions"], report["current_snapshot"]["questions"])

    def test_missing_frozen_context_and_changed_phase_conditions_fall_back(self):
        report, script = self.run_cycle(conditions=None)
        self.assertEqual(len(script.calls), 27)
        self.assertEqual(report["within_run_review_reuse"]["final_review"]["reason"], "missing_frozen_execution_context")
        for field, value in (("max_tokens", 8192), ("reviewer_model", "final_review.critic")):
            config = same_plan(); config["phases"]["final_review"][field] = value
            with self.subTest(field=field):
                report, script = self.run_cycle(config=config)
                self.assertEqual(report["budget"]["phase_provider_attempts"]["final_review"], 12)
                self.assertEqual(report["within_run_review_reuse"]["final_review"]["reason"], "phase_conditions_differ")

    def test_changed_transport_or_source_evidence_never_reuses(self):
        for target in ("transport", "source"):
            config = same_plan(); supplied = context(config)
            def checkpoint(event):
                if event["phase"] == "question_edit":
                    if target == "transport": supplied["transport"]["profiles"]["explicit"]["deadline_seconds"] = 301
                    else: supplied["source_sha256"]["pipeline/semantic_review.py"] = "0" * 64
            with self.subTest(target=target):
                report, script = self.run_cycle(config=config, conditions=supplied, on_phase=checkpoint)
                self.assertEqual(len(script.calls), 27)
                self.assertTrue(report["receipt"]["final_review_is_fresh"])
                self.assertTrue(all(x["mode"] == "fresh" for x in report["within_run_review_reuse"].values()))

    def test_initial_pending_review_not_reused_and_provider_failure_still_stops(self):
        report, script = self.run_cycle(script=OpinionScript(pending_initial=True))
        self.assertTrue(any(i["review_state"] != "completed" for i in report["phases"]["question_review"]["items"]))
        self.assertEqual(report["budget"]["phase_provider_attempts"]["final_review"], 12)
        self.assertEqual(report["within_run_review_reuse"]["final_review"]["reason"], "initial_review_incomplete_or_binding_invalid")
        calls = []
        def fail(*a, **kw): calls.append(a); raise TimeoutError("OFFLINE timeout")
        with self.assertRaises(factory.AgentCaseError): self.run_cycle(script=fail)
        self.assertEqual(len(calls), 1)

    def test_cycle_cli_exports_reuse_accepted_by_existing_formal_consumer(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); old = base / "old/execution"; old.mkdir(parents=True)
            write_upstream_fixture(old, self.upstream)
            case = base / "new"; case.mkdir()
            config = same_plan(cap=14)
            prepare_fixture_case(case, old, config=config, transport=transport())
            script = OpinionScript()
            with patch.object(cycle, "provider", return_value=(script, lambda e: None, deepcopy)):
                result = cycle.run(case, execute=True)
            self.assertEqual(result["actual_provider_attempts"], 14)
            blobs, lineage = agent_pipeline._upstream(case)
            final = json.loads(blobs["review"])
            self.assertEqual(final["within_run_reuse"]["new_provider_calls"], 0)
            self.assertEqual(lineage["original_count"], 4)
            helper = entry_fixtures.EntryTests(methodName="runTest"); helper.base = base; helper.config = base / "spec"; helper.config.mkdir()
            spec = helper.eval_spec(case)
            with patch.object(evaluation, "check_calibration", return_value={"files": {}}):
                ready = agent_pipeline.prepare_evaluation(spec, base / "evaluation")
            self.assertTrue(ready["ready"])
            self.assertFalse((base / "evaluation/execution").exists())
            self.assertEqual(json.loads((case / "execution/attempt_count.json").read_bytes())["lineage_total_provider_dispatches"],
                14 + self.upstream["budget"]["actual_provider_attempts"])

    def test_generation_cli_supplies_frozen_context_without_changing_default_provider(self):
        class KeepTeam(TeamScript):
            def __call__(self, step, messages, **params):
                if params["model"] == "question_edit":
                    payload = json.loads(messages[1]["content"])
                    self.calls.append({"phase": "question_edit", "step": step})
                    return {"decisions": [{"qid": q["qid"], "action": "keep", "reason": "OFFLINE keep negative reference"}
                        for q in payload["questions"]], "issue_responses": [{"issue_id": i["issue_id"],
                        "disposition": "disputed", "reason": "OFFLINE opinion is not approval"} for i in payload["issues"]]}
                return super().__call__(step, messages, **params)
        with tempfile.TemporaryDirectory() as temp:
            case = Path(temp)
            for name in ("seed.json", "design_intent.json", "question_intent.json"):
                (case/name).write_text('{"purpose":"OFFLINE"}', encoding="utf-8")
            (case/'protocol.txt').write_text(PROTOCOL, encoding="utf-8")
            config = team_plan(19); config['review_reuse'] = factory.REVIEW_REUSE_POLICY
            for a,b in (("question_review","final_review"),("collection_review","final_collection_review")):
                config['phases'][b] = deepcopy(config['phases'][a])
            plan = {'version':generation.TEAM_PLAN_VERSION,'result_scope':'research_only','factory_plan':config,
                'transport':transport(), 'input_sha256':{n:hashlib.sha256((case/n).read_bytes()).hexdigest()
                    for n in generation.INPUTS if n!='plan.json'},
                'source_sha256':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest()
                    for n in generation.source_names(config,transport=transport())}}
            (case/'plan.json').write_text(json.dumps(plan),encoding='utf-8')
            script=KeepTeam()
            with patch.object(generation,'provider',return_value=(script,lambda e:None,deepcopy)):
                generation.run(case,execute=True)
            report=json.loads((case/'execution/report.json').read_bytes())
            self.assertTrue(report['phases']['question_edit']['candidate_ready'])
            self.assertTrue(report['phases']['question_edit']['editorial_completion']['complete'])
            self.assertEqual(report['within_run_review_reuse']['final_review']['mode'],'reused')
            self.assertEqual(report['within_run_review_reuse']['final_collection_review']['mode'],'reused')
            self.assertFalse(report['receipt']['final_review_is_fresh'])
            self.assertEqual(report['budget']['phase_provider_attempts']['final_review'],0)


if __name__ == '__main__': unittest.main()
