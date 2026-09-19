"""Offline continuation lineage, fresh roles, stop latches and persistence checks."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline import agent_factory as factory
from pipeline.quality_workflow import validate_review
from tools import run_agent_question_cycle as cli
from agent_factory_selftest import TeamScript, team_plan, run as run_factory
from structured_reference_workflow_selftest import StructuredScript, REAL_REPORT


def failed_upstream(*, partial=False):
    class FourStructured(TeamScript):
        def __call__(self, step, messages, **params):
            if partial and params["model"] == "question_review.auditor":
                raise TimeoutError("OFFLINE_PARTIAL_UPSTREAM_PROVIDER_FAILURE")
            value = super().__call__(step, messages, **params)
            if params["model"] == "question_generation":
                value["questions"] *= 2
                value["questions"] = deepcopy(value["questions"])
                for i in range(4):
                    value["questions"][i] = deepcopy(value["questions"][i])
                    value["questions"][i]["question"] += str(i)
                    value["questions"][i]["reference_proposal"]["answer"] = {"original": ["OFFLINE_" + str(i), i]}
            return value
    config = team_plan(40)
    config["question_batches"][0]["count"] = 4
    if partial:
        config["phases"]["question_review"].update(max_calls=12, workers=1,
            reference_auditor_model="question_review.auditor")
        try:
            run_factory(FourStructured(), config)
        except factory.AgentCaseError as exc:
            return exc.report
        raise AssertionError("Expected partial initial-review provider failure")
    with patch.object(factory.quality_workflow, "fresh_review", side_effect=ValueError("OLD_LOCAL_PREPARATION_FAILURE")):
        try:
            run_factory(FourStructured(), config)
        except factory.AgentCaseError as exc:
            return exc.report
    raise AssertionError("Expected fixture failure before first question request")


def cycle_plan(cap=27, workers=1):
    phases = {name: deepcopy(team_plan()["phases"][name]) for name in factory.QUESTION_CYCLE_PHASES}
    for name in factory.REVIEW_PHASES:
        phases[name].update(reference_auditor_model=name + ".auditor", max_calls=12, workers=workers)
    return {"max_provider_attempts": cap, "phases": phases}


class CycleScript(StructuredScript):
    def __call__(self, step, messages, **params):
        payload = json.loads(messages[1]["content"])
        if params["model"] in {"collection_review", "final_collection_review", "question_edit"}:
            self.calls.append({"step": step, "payload": payload, "params": deepcopy(params)})
            if params["model"] == "question_edit":
                return {"decisions": [{"qid": q["qid"], "action": "keep", "reason": "OFFLINE_FIXTURE"}
                    for q in payload["questions"]], "issue_responses": [{"issue_id": i["issue_id"],
                    "disposition": "addressed", "reason": "OFFLINE_FIXTURE"} for i in payload["issues"]]}
            return {"assessment": "OFFLINE_FIXTURE", "issues": [], "overlap_groups": [],
                "coverage_observations": [], "limitations": [], "evidence": []}
        return super().__call__(step, messages, **params)


def write_upstream_fixture(directory, report):
    """Build complete synthetic offline evidence, never a real provider record."""
    def write(name, value):
        path = directory / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    write("failed_report.json", report)
    write("failure.json", {"status": "failed_execution_preserved", "error_type": "AgentCaseError"})
    write("attempt_count.json", report["budget"])
    write("phases/question_generation/report.json", report["phases"]["question_generation"])
    input_blobs = {name + ".json": json.dumps(report["inputs"][name], ensure_ascii=False).encode("utf-8")
                   for name in ("seed", "design_intent", "question_intent")}
    input_blobs["protocol.txt"] = report["inputs"]["public_protocol"].encode("utf-8")
    old_sources = {name: (ROOT / name).read_bytes() for name in cli.case_source_names(report["plan"])}
    old_plan = {"version": "agent-case-plan/v2", "result_scope": "research_only", "factory_plan": report["plan"],
        "input_sha256": {name: cli._hash(blob) for name, blob in input_blobs.items()},
        "source_sha256": {name: cli._hash(blob) for name, blob in old_sources.items()}}
    input_blobs["plan.json"] = json.dumps(old_plan).encode("utf-8")
    for name, blob in input_blobs.items():
        (directory.parent / name).write_bytes(blob)
        path = directory / "inputs" / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(blob)
    for name, blob in old_sources.items():
        path = directory / "source_snapshot" / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(blob)
    write("manifest.json", {"case": str(directory.parent), "result_scope": "research_only",
        "input_sha256": {name: cli._hash(blob) for name, blob in input_blobs.items()},
        "source_sha256": old_plan["source_sha256"]})
    rows = []
    for index, attempt in enumerate(report["provider_attempts"]):
        common = {"step": attempt["step"], "operation_id": "offline_operation_" + str(index)}
        attempt_id, call_id = "offline_attempt_" + str(index), "offline_call_" + str(index)
        returned = attempt["status"] == "returned"
        rows.extend([{**common, "event": "json_attempt", "attempt_id": attempt_id, "attempt": 1, "max_attempts": 1},
            {**common, "event": "request", "call_id": call_id, "messages": attempt["messages"],
                "model": attempt["params"]["model"], "sdk_options": {"max_retries": 0}},
            {**common, "event": "response" if returned else "call_error", "call_id": call_id},
            {**common, "event": "json_result" if returned else "json_error", "attempt_id": attempt_id,
                **({"parsed": attempt["raw_output"]} if returned else {})}])
    (directory / "attempts.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    (directory / "calls.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in report["records"]), encoding="utf-8")


def prepare_fixture_case(case, upstream, *, kind="initial_review_failure", config=None, transport=None):
    manifest = json.loads((upstream / "manifest.json").read_bytes())
    names = cli.upstream_files(kind, manifest)
    for name in names:
        target = case / "upstream" / name; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((upstream / name).read_bytes())
    report = json.loads((upstream / cli.UPSTREAM_KINDS[kind][0]).read_bytes())
    (case / "snapshot.json").write_text(json.dumps(report["original_snapshot"], ensure_ascii=False), encoding="utf-8")
    config = config or cycle_plan()
    value = {"version": cli.PLAN_VERSION, "result_scope": "research_only", "cycle_plan": config,
        "upstream_kind": kind, "upstream_execution_directory": str(upstream), "transport": transport or {
            "version": "chat-completions-transport/v1", "default_profile": "explicit", "model_profiles": {},
            "profiles": {"explicit": {"token_limit_parameter": "max_completion_tokens", "reasoning_effort": "high",
                "response_format": {"type": "json_object"}, "http_timeout_seconds": 300, "deadline_seconds": 300}}},
        "input_sha256": {name: cli._hash((case / name).read_bytes())
            for name in ["snapshot.json", *("upstream/" + n for n in names)]},
        "source_sha256": {name: cli._hash((ROOT / name).read_bytes()) for name in cli.source_names(config)}}
    (case / "plan.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return value


class CycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = failed_upstream()

    def run_cycle(self, script=None, plan=None, **kwargs):
        script = script or CycleScript()
        return factory.run_agent_question_cycle(self.upstream["original_snapshot"], self.upstream,
            plan=plan or cycle_plan(), chat_json=script, **kwargs), script

    def test_exact_27_calls_shared_cycle_fresh_reviews_and_structured_originals(self):
        before = deepcopy(self.upstream)
        with patch.object(factory, "_question_cycle", wraps=factory._question_cycle) as core:
            report, script = self.run_cycle(plan=cycle_plan(workers=3))
        self.assertEqual(core.call_count, 1)
        self.assertEqual(report["execution"]["status"], "completed")
        self.assertEqual(len(script.calls), 27)
        self.assertEqual(report["budget"]["actual_provider_attempts"], 27)
        self.assertEqual(report["upstream_budget"], before["budget"])
        self.assertEqual(self.upstream, before)
        self.assertEqual(report["original_snapshot"]["questions"], report["current_snapshot"]["questions"])
        self.assertEqual(report["receipt"]["original_candidate_count"], 4)
        self.assertEqual(report["receipt"]["requested_question_count"], 4)
        self.assertEqual(report["receipt"]["new_questions_from_revision"], 0)
        self.assertFalse(report["receipt"]["scoring_ready"])
        validate_review(report["current_snapshot"], report["phases"]["final_review"])
        finals = [c for c in script.calls if c["params"]["model"].startswith("final_review.")]
        self.assertEqual(len(finals), 12)
        for call in finals:
            if call["step"].endswith("blind_read"):
                self.assertNotIn("reference_proposal", call["payload"])
                self.assertNotIn("issues", call["payload"])
            if "original_reference" in call["payload"]:
                self.assertIsInstance(call["payload"]["original_reference"]["answer"], dict)
        editor = next(c for c in script.calls if c["params"]["model"] == "question_edit")
        issue = json.loads(editor["payload"]["issues"][0]["description"].split("\n", 1)[1])
        self.assertIn("reference_location_policy", issue["reference_audit"])
        self.assertTrue(issue["reference_audit"]["claim_locations"])
        self.assertNotIn("resolved_evidence", issue["reference_audit"]["claim_locations"][0])

    def test_real_original_four_references_and_nine_prior_requests_validate_without_calls(self):
        if not REAL_REPORT.exists():
            self.skipTest("Local original artifact is not distributed")
        upstream = json.loads(REAL_REPORT.read_text(encoding="utf-8"))
        prepared = factory.prepare_question_cycle(upstream["original_snapshot"], upstream, cycle_plan())
        self.assertEqual(prepared["binding"]["upstream_provider_attempts"], 9)
        self.assertEqual(prepared["original_snapshot"], upstream["original_snapshot"])

    def test_partial_initial_review_keeps_old_attempts_and_never_reuses_old_opinions(self):
        upstream = failed_upstream(partial=True)
        self.assertEqual(upstream["budget"]["phase_provider_attempts"]["question_review"], 2)
        marker = "OLD_UPSTREAM_REVIEW_ERROR_NOT_FOR_NEW_WIRE"
        upstream["phases"]["question_review"]["items"][0]["blind_read"]["reasoning"] = marker
        upstream["old_grade_and_error"] = marker
        before, script = deepcopy(upstream), CycleScript()
        report = factory.run_agent_question_cycle(upstream["original_snapshot"], upstream,
            plan=cycle_plan(), chat_json=script)
        self.assertEqual(upstream, before)
        self.assertEqual(len(script.calls), 27)
        self.assertEqual(report["upstream_budget"], before["budget"])
        self.assertEqual(report["binding"]["upstream_provider_attempts"], before["budget"]["actual_provider_attempts"])
        self.assertEqual(report["binding"]["upstream_kind"], "initial_review_failure")
        self.assertNotIn(marker, json.dumps(script.calls))
        self.assertEqual(report["receipt"]["original_count"], 4)
        self.assertEqual(report["budget"]["actual_provider_attempts"], 27)

    def test_tampered_snapshot_acceptance_denominator_and_lineage_fail_before_calls(self):
        changes = [lambda r: r["original_snapshot"]["questions"][0]["reference_proposal"].update(answer="new"),
            lambda r: r["material_acceptance"].update(corpus_hash="wrong"),
            lambda r: r["phases"]["question_generation"]["quantity"].update(requested=3),
            lambda r: r["phases"]["question_generation"]["candidates"].pop(),
            lambda r: r["budget"]["phase_provider_attempts"].update(question_review=1),
            lambda r: r["provider_attempts"].pop(), lambda r: r["execution"].update(status="completed")]
        for change in changes:
            upstream = deepcopy(self.upstream); change(upstream)
            with self.subTest(change=change), self.assertRaises(ValueError):
                factory.prepare_question_cycle(upstream["original_snapshot"], upstream, cycle_plan())

    def test_first_transport_failure_stops_all_following_roles_and_retains_raw(self):
        calls = []
        def failed(*a, **kw):
            calls.append(a); raise TimeoutError("OFFLINE_TIMEOUT")
        with self.assertRaises(factory.AgentCaseError) as caught:
            self.run_cycle(failed)
        self.assertEqual(len(calls), 1)
        self.assertEqual(caught.exception.report["budget"]["actual_provider_attempts"], 1)
        self.assertIn("fatal_failures", caught.exception.report)
        self.assertNotIn("collection_review", caught.exception.report["phases"])

    def test_schema_failure_stops_before_auditor_and_preserves_exact_bad_output(self):
        calls = []
        def invalid(*a, **kw):
            calls.append(a); return {"OFFLINE_MALFORMED": "full raw return"}
        with self.assertRaises(factory.AgentCaseError) as caught:
            self.run_cycle(invalid)
        self.assertEqual(len(calls), 1)
        self.assertEqual(caught.exception.report["provider_attempts"][0]["raw_output"],
                         {"OFFLINE_MALFORMED": "full raw return"})

    def test_pre_dispatch_audit_failure_latches_before_any_provider_attempt(self):
        script = CycleScript()
        with self.assertRaises(factory.AgentCaseError) as caught:
            self.run_cycle(script, before_call=lambda a: (_ for _ in ()).throw(OSError("reserve failed")))
        self.assertEqual(script.calls, [])
        self.assertEqual(caught.exception.report["budget"]["actual_provider_attempts"], 0)

    def test_record_and_checkpoint_failures_halt_without_losing_return(self):
        for callback in ("record", "on_phase"):
            script = CycleScript()
            def fail(event):
                if callback == "on_phase" or event.get("event") == "finished":
                    raise OSError("OFFLINE_AUDIT")
            with self.subTest(callback=callback), self.assertRaises(factory.AgentCaseError) as caught:
                self.run_cycle(script, **{callback: fail})
            self.assertEqual(len(script.calls), 1 if callback == "record" else 12)
            self.assertIsNotNone(caught.exception.report["provider_attempts"][0]["raw_output"])

    def test_lower_budget_does_not_refill_or_hide_original_denominator(self):
        report, script = self.run_cycle(plan=cycle_plan(cap=3))
        self.assertEqual(len(script.calls), 3)
        self.assertEqual(report["receipt"]["original_count"], 4)
        self.assertEqual(report["receipt"]["counts"]["unresolved"], 4)
        self.assertFalse(report["receipt"]["scoring_ready"])

    def test_more_than_27_or_nonisolated_plan_is_rejected(self):
        plans = [cycle_plan(cap=28), cycle_plan(), cycle_plan()]
        plans[1]["phases"]["question_review"].pop("reference_auditor_model")
        plans[2]["phases"]["question_review"]["max_calls"] = 13
        for plan in plans:
            with self.assertRaises(ValueError):
                factory.prepare_question_cycle(self.upstream["original_snapshot"], self.upstream, plan)


class CycleCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.case, self.old = self.root / "new", self.root / "old" / "execution"
        self.case.mkdir(); self.old.mkdir(parents=True)
        self.upstream = failed_upstream()
        write_upstream_fixture(self.old, self.upstream)
        self.plan = prepare_fixture_case(self.case, self.old)

    def write_plan(self):
        (self.case / "plan.json").write_text(json.dumps(self.plan), encoding="utf-8")

    def inject(self, script=None):
        return patch.object(cli, "provider", return_value=(script or CycleScript(), lambda e: None, deepcopy))

    def test_dry_run_imports_no_credentials_and_writes_nothing(self):
        code = """import builtins,sys
original=builtins.__import__
def checked(name,*a,**kw):
 if name.split('.')[0] in {'config','dotenv','openai'}: raise AssertionError(name)
 return original(name,*a,**kw)
builtins.__import__=checked
from tools.run_agent_question_cycle import run
assert run(sys.argv[1])['calls_permitted']==0
"""
        result = subprocess.run([sys.executable, "-B", "-c", code, str(self.case)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.case / "execution").exists())

    def test_execute_persists_separate_lineage_raw_and_rejects_restart(self):
        script = CycleScript()
        old = {name: (self.old / name).read_bytes() for name in cli.UPSTREAM_FILES}
        with self.inject(script) as provider:
            result = cli.run(self.case, execute=True)
        self.assertEqual(len(script.calls), 27)
        self.assertEqual(result["upstream_provider_attempts"], self.upstream["budget"]["actual_provider_attempts"])
        self.assertEqual(provider.call_args.kwargs["transport"], self.plan["transport"])
        out = self.case / "execution"
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["runtime"]["python_executable"], sys.executable)
        self.assertEqual(manifest["runtime"]["python_version"], sys.version)
        self.assertEqual(len((out / "reservations.jsonl").read_text(encoding="utf-8").splitlines()), 27)
        self.assertEqual(json.loads((out / "post_run_freeze_verification.json").read_text())["status"], "matches")
        self.assertTrue((out / "phases/final_collection_review/report.json").exists())
        self.assertTrue((out / "source_snapshot/pipeline/reference_locations.py").exists())
        self.assertEqual(old, {name: (self.old / name).read_bytes() for name in old})
        with self.assertRaises(FileExistsError): cli.run(self.case, execute=True)

    def test_hash_and_upstream_mutation_block_before_provider(self):
        (self.old / "failure.json").write_text("{}", encoding="utf-8")
        with patch.object(cli, "provider") as provider, self.assertRaises(ValueError):
            cli.run(self.case, execute=True)
        provider.assert_not_called()
        self.assertFalse((self.case / "execution").exists())

    def test_source_list_includes_all_shared_modules_and_rejects_missing_hash(self):
        self.plan["source_sha256"].pop("pipeline/reference_locations.py"); self.write_plan()
        with patch.object(cli, "provider") as provider, self.assertRaises(ValueError):
            cli.run(self.case, execute=True)
        provider.assert_not_called()

    def test_legacy_kind_default_and_partial_failure_copies_remain_supported(self):
        self.plan.pop("upstream_kind"); self.write_plan()
        self.assertTrue(cli.run(self.case)["ready"])
        partial = failed_upstream(partial=True)
        write_upstream_fixture(self.old, partial)
        self.plan = prepare_fixture_case(self.case, self.old)
        result = cli.run(self.case)
        self.assertEqual(result["lineage"]["upstream_budget"], partial["budget"])
        self.assertEqual(result["lineage"]["upstream_wire_audit"]["provider_errors"], 1)
        self.assertFalse((self.case / "execution").exists())

    def test_upstream_wire_reconciliation_checks_identity_raw_and_retry_without_order_assumption(self):
        prepared = cli._prepare(self.case)
        blobs = deepcopy(prepared["upstream_blobs"])
        rows = [json.loads(line) for line in blobs["attempts.jsonl"].splitlines()]
        def packed(value):
            return "".join(json.dumps(r) + "\n" for r in value).encode("utf-8")
        blobs["attempts.jsonl"] = packed(list(reversed(rows)))
        self.assertEqual(cli._upstream_ledger(blobs, self.upstream)["requests"], len(self.upstream["provider_attempts"]))
        for mutation in ("duplicate_id", "missing_terminal", "wrong_message", "wrong_raw", "retry"):
            changed = deepcopy(rows)
            requests = [r for r in changed if r["event"] == "request"]
            if mutation == "duplicate_id": requests[1]["call_id"] = requests[0]["call_id"]
            elif mutation == "missing_terminal": changed.pop(2)
            elif mutation == "wrong_message": requests[0]["messages"] = []
            elif mutation == "wrong_raw": next(r for r in changed if r["event"] == "json_result")["parsed"] = {}
            else: next(r for r in changed if r["event"] == "json_attempt")["max_attempts"] = 2
            blobs["attempts.jsonl"] = packed(changed)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                cli._upstream_ledger(blobs, self.upstream)

    def test_original_input_or_source_tampering_is_rejected_even_if_new_copy_hash_is_updated(self):
        for name in ("inputs/protocol.txt", "source_snapshot/pipeline/agent_factory.py"):
            old, copied = self.old / name, self.case / "upstream" / name
            original = old.read_bytes()
            try:
                old.write_bytes(original + b"\nchanged")
                copied.write_bytes(old.read_bytes())
                self.plan["input_sha256"]["upstream/" + name] = cli._hash(copied.read_bytes())
                self.write_plan()
                with self.subTest(name=name), patch.object(cli, "provider") as provider, self.assertRaises(ValueError):
                    cli.run(self.case, execute=True)
                provider.assert_not_called()
                self.assertFalse((self.case / "execution").exists())
            finally:
                old.write_bytes(original); copied.write_bytes(original)
                self.plan["input_sha256"]["upstream/" + name] = cli._hash(original)
                self.write_plan()

    def test_reservation_write_failure_has_zero_calls_and_preserved_failed_report(self):
        script = CycleScript()
        with self.inject(script), patch.object(cli, "_reserve", side_effect=OSError("disk")), self.assertRaises(factory.AgentCaseError):
            cli.run(self.case, execute=True)
        self.assertEqual(script.calls, [])
        out = self.case / "execution"
        self.assertTrue((out / "failed_report.json").exists())
        self.assertEqual(json.loads((out / "attempt_count.json").read_text())["new_provider_dispatches"], 0)

    def test_change_after_first_call_stops_before_second(self):
        script = CycleScript()
        def mutate(step, messages, **params):
            value = script(step, messages, **params)
            (self.case / "plan.json").write_text("{}", encoding="utf-8")
            return value
        with self.inject(mutate), self.assertRaises(factory.AgentCaseError):
            cli.run(self.case, execute=True)
        self.assertEqual(len(script.calls), 1)
        self.assertEqual(json.loads((self.case / "execution/post_run_freeze_verification.json").read_text())["status"], "mismatch")

    def test_provider_initialization_failure_keeps_zero_attempt_lineage(self):
        with patch.object(cli, "provider", side_effect=RuntimeError("init")), self.assertRaises(RuntimeError):
            cli.run(self.case, execute=True)
        count = json.loads((self.case / "execution/attempt_count.json").read_text())
        self.assertEqual(count["new_provider_dispatches"], 0)
        self.assertEqual(count["upstream_budget"], self.upstream["budget"])


class CompletedGapRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream_dir = ROOT / "output/implementation_bc_agent_loop_20260917/pagen4/execution"
        if not (cls.upstream_dir / "report.json").exists():
            raise unittest.SkipTest("Local real completed-gap artifact is not distributed")
        cls.real = json.loads((cls.upstream_dir / "report.json").read_bytes())

    def test_real_completed_report_preserves_38_attempts_and_four_structured_originals(self):
        before = deepcopy(self.real)
        prepared = factory.prepare_question_cycle(self.real["original_snapshot"], self.real, cycle_plan())
        self.assertEqual(self.real, before)
        self.assertEqual(prepared["binding"]["upstream_kind"], "completed_unedited_execution_gaps")
        self.assertEqual(prepared["binding"]["upstream_provider_attempts"], 38)
        self.assertEqual(prepared["binding"]["upstream_execution"]["status"], "completed_with_execution_gaps")
        self.assertEqual(prepared["denominator"]["original_candidate_count"], 4)
        self.assertTrue(all(isinstance(q["reference_proposal"]["answer"], dict)
                            for q in prepared["original_snapshot"]["questions"]))

    def test_real_changed_inputs_editor_receipt_and_old_counts_cannot_claim_unedited_recovery(self):
        changes = [lambda r: r["current_snapshot"]["questions"][0].update(question="CHANGED"),
            lambda r: r["current_snapshot"]["corpus"]["sessions"][0]["docs"][0].update(content="CHANGED"),
            lambda r: r["current_snapshot"].update(protocol="CHANGED"),
            lambda r: r["phases"]["question_edit"].update(candidate_ready=True),
            lambda r: r["receipt"]["counts"].update(unresolved=3, model_review_eligible=1),
            lambda r: r["receipt"].update(original_candidate_count=3),
            lambda r: r["budget"].update(actual_provider_attempts=0),
            lambda r: r["provider_attempts"].pop(),
            lambda r: r["phases"]["question_generation"]["batch_reports"][0]["raw_output"]["questions"].pop()]
        for index, change in enumerate(changes):
            report = deepcopy(self.real); change(report)
            with self.subTest(index=index), self.assertRaises(ValueError):
                factory.prepare_question_cycle(report["original_snapshot"], report, cycle_plan())

    def test_real_completed_cli_dry_run_uses_actual_report_and_old_sources_without_failure_fabrication(self):
        with tempfile.TemporaryDirectory() as temp:
            case = Path(temp)
            plan = prepare_fixture_case(case, self.upstream_dir, kind="completed_unedited_execution_gaps")
            with patch.object(cli, "provider") as provider:
                result = cli.run(case)
            provider.assert_not_called()
            self.assertEqual(result["calls_permitted"], 0)
            self.assertEqual(result["lineage"]["upstream_wire_audit"],
                {"requests": 38, "responses": 20, "provider_errors": 18, "json_results": 20, "json_errors": 18})
            self.assertNotIn("upstream/failure.json", plan["input_sha256"])
            self.assertFalse((case / "upstream/failed_report.json").exists())
            self.assertFalse((case / "execution").exists())
            old_factory = case / "upstream/source_snapshot/pipeline/agent_factory.py"
            self.assertNotEqual(old_factory.read_bytes(), (ROOT / "pipeline/agent_factory.py").read_bytes())
            self.assertEqual(result["lineage"]["upstream_execution"]["status"], "completed_with_execution_gaps")

    def test_real_completed_inputs_mock_new_cycle_exports_a_valid_fresh_downstream_case(self):
        from tools.agent_pipeline import _upstream
        with tempfile.TemporaryDirectory() as temp:
            case = Path(temp) / "offline"; case.mkdir()
            prepare_fixture_case(case, self.upstream_dir, kind="completed_unedited_execution_gaps")
            script = CycleScript()
            with patch.object(cli, "provider", return_value=(script, lambda e: None, deepcopy)):
                result = cli.run(case, execute=True)
            self.assertEqual(len(script.calls), 27)
            self.assertEqual(result["upstream_provider_attempts"], 38)
            self.assertEqual(result["lineage_total_provider_dispatches"], 65)
            self.assertEqual(result["execution"]["status"], "completed")
            _upstream(case)
            report = json.loads((case / "execution/report.json").read_bytes())
            self.assertEqual(report["receipt"]["original_count"], 4)
            self.assertEqual(report["original_snapshot"], self.real["original_snapshot"])
            old_error = self.real["execution"]["gaps"][0]["execution"]["message"]
            self.assertNotIn(old_error, json.dumps(script.calls, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
