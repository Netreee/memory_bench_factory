"""Offline CLI integration with a synthetic upstream and an explicit fake trace.

No real provider/configuration is loaded. Scripted answers and collection prose
exercise transport and isolation only; they are not semantic quality evidence.
"""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from tools import run_agent_context_challenge as cli
from tools.run_agent_evaluation import ANSWER_SYSTEM
from pipeline.quality_workflow import snapshot, make_receipt
from eval.answer_task_review import append_scoring_policy
from eval.provenance import digest
from llm_transport import VERSION, build_request_parameters, resolve_profile
from llm_trace import trace_scope, emit
from agent_evaluation_selftest import reference_report


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*") if p.is_file()}


def transport():
    return {"version": VERSION, "default_profile": "high_json", "model_profiles": {},
        "profiles": {"high_json": {"token_limit_parameter": "max_completion_tokens",
            "reasoning_effort": "high", "omit_parameters": ["temperature", "top_p"],
            "response_format": {"type": "json_object"},
            "http_timeout_seconds": 300, "deadline_seconds": 300}}}


class FakeProvider:
    """Provider injection with the actual ledger's necessary trace fields."""
    def __init__(self, mode="ok", after_return=None):
        self.mode = mode
        self.after_return = after_return
        self.calls = []
        self.records = []

    def __call__(self, directory, *, transport):
        def record(event):
            self.records.append(deepcopy(event))
            if self.mode == "record_finished" and event["event"] == "finished":
                raise OSError("OFFLINE scripted callback failure")
            with (directory / "calls.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")

        def call(step, messages, **params):
            index = len(self.calls) + 1
            self.calls.append({"step": step, "messages": deepcopy(messages), "params": deepcopy(params)})
            model = params["model"]
            profile = resolve_profile(transport, model)
            wire = build_request_parameters(model=model, max_tokens=params["max_tokens"],
                temperature=params.get("temperature", 0.7), top_p=params.get("top_p", 1.0),
                transport=transport)
            wire["deadline_s"] = profile["profile"]["deadline_seconds"]
            if self.mode == "wrong_wire" and index == 1:
                wire["max_completion_tokens"] += 1
            call_id, attempt_id = "fake-call-" + str(index), "fake-attempt-" + str(index)
            with trace_scope(directory / "attempts.jsonl", step):
                emit("json_attempt", attempt_id=attempt_id, attempt=1, max_attempts=1, strict_json=True)
                emit("request", call_id=call_id, model=model, messages=messages, parameters=wire,
                    transport=profile, sdk_options={"max_retries": 0, "timeout_seconds": 300})
                if self.mode == "provider403" and index == 1:
                    emit("call_error", call_id=call_id, error_type="PermissionError", message="OFFLINE 403")
                    emit("json_error", attempt_id=attempt_id, error_type="PermissionError", message="OFFLINE 403")
                    raise PermissionError("OFFLINE scripted provider 403")
                if step == "context_challenge.answer":
                    raw = {"answer": "OFFLINE fixture: the selected evidence does not establish the missing early fact."}
                else:
                    raw = {"assessment": "OFFLINE collection observation; no accuracy claim.",
                        "issues": [], "overlap_groups": [],
                        "coverage_observations": ["OFFLINE unknown remains evidence-limited, not automatically wrong."],
                        "limitations": ["Scripted response, not an empirical semantic result."], "evidence": []}
                if self.mode == "invalid_answer" and index == 1:
                    raw = {"answer": "  "}
                observed = {"answer": "DIFFERENT OFFLINE raw"} if self.mode == "wrong_raw" and index == 1 else raw
                emit("response", call_id=call_id, response={"id": "fake-provider-" + str(index),
                    "model": model, "usage": {"total_tokens": 10},
                    "choices": [{"content": json.dumps(observed, ensure_ascii=False)}]})
                emit("json_result", attempt_id=attempt_id, parse_mode="complete", parsed=raw)
            if self.mode == "broken_trace" and index == 1:
                with (directory / "attempts.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write("{OFFLINE broken trace\n")
            if self.after_return is not None:
                self.after_return(index, directory)
            return raw
        return call, record, deepcopy


class ContextChallengeCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="offline-context-cli-")
        self.addCleanup(self.temp.cleanup)
        # Windows may return the 8.3 spelling of the temporary user directory;
        # the production CLI resolves it before dispatch and callback paths.
        self.base = Path(self.temp.name).resolve()
        self.net = patch("socket.socket.connect", side_effect=AssertionError("Network forbidden in selftest"))
        self.net.start()
        self.addCleanup(self.net.stop)
        self.source = self.base / "synthetic upstream"
        self.specdir = self.base / "spec folder"
        self.specdir.mkdir()
        self.protocol = append_scoring_policy("Use only public evidence; state missing information honestly.")
        self.corpus = [{"doc_id": "d00000" + str(i), "content": text,
                        "session": (i - 1) // 2, "date": "2024-03-" + ("20" if i < 3 else "27")}
            for i, text in enumerate(["EARLY_ONLY_A. Report published on 2024-03-20.",
                                     "EARLY_ONLY_B. Review completed on 2024-03-20.",
                                     "LATE_C. One later record remains available.",
                                     "LATE_D. No exact completion date is supplied here."], 1)]
        questions = [{"qid": "q" + str(i), "question": "What does the record establish for item " + str(i) + "?",
                      "reference_proposal": {"answer": "PRIVATE_REFERENCE_SENTINEL", "rationale": "PRIVATE_REASON_SENTINEL"}}
                     for i in (1, 2)]
        original = snapshot(questions, self.corpus, self.protocol)
        current = snapshot(questions, self.corpus, self.protocol, parent=original)
        review = reference_report(questions, self.corpus, self.protocol, isolated=True)
        receipt = make_receipt(original, current, review, material_ready=True)
        report = {"version": "agent-case-factory/v2", "result_scope": "research_only", "official_release": False,
            "execution": {"status": "completed"}, "current_snapshot": current, "original_snapshot": original,
            "receipt": receipt, "phases": {"final_review": review}, "material_ready": True,
            "material_acceptance": {"status": "accepted", "authority": "model", "correctness_verified": False,
                "corpus_hash": digest(current["corpus"]), "protocol_hash": digest(current["protocol"])}}
        for name, value in {"report": report, "original_snapshot": original, "snapshot": current,
                            "receipt": receipt, "phases/final_review/report": review}.items():
            write(self.source / "execution" / (name + ".json"), value)
        write(self.source / "formal_grading_sentinel.json", {"status": "OFFLINE unchanged preexisting artifact"})
        self.protected = hashes(self.source)
        self.spec = write(self.specdir / "challenge.json", {"version": cli.SPEC_VERSION,
            "source_case": os.path.relpath(self.source, self.specdir),
            "trials": [{"id": "t" + str(i), "qid": "q" + str(i),
                        "doc_ids": ["d000003", "d000004"], "reason": "DESIGN_PURPOSE_NOT_SOLVER_INPUT"} for i in (1, 2)],
            "solver": {"model": "offline-solver", "max_tokens": 16384},
            "interpreter": {"model": "offline-interpreter", "max_tokens": 8192},
            "transport": transport(), "max_provider_attempts": 3,
            "max_input_chars": 500000, "full_context_char_budget": 120000})
        self.case = self.base / "case"

    def tearDown(self):
        self.assertEqual(hashes(self.source), self.protected, "Original generation/receipt/grade files changed")

    def prepared(self):
        with patch.object(cli, "provider", side_effect=AssertionError("Provider must not initialize in prepare")):
            result = cli.prepare(self.spec, self.case)
        self.assertTrue(result["ready"])
        self.assertFalse((self.case / "execution").exists())
        return result

    def execute(self, fake):
        with patch.object(cli, "provider", fake), redirect_stdout(io.StringIO()):
            value = cli.run(self.case, execute=True)
        return value, cli.read(self.case / "execution/report.json")

    def assert_stopped_first(self, mode):
        self.prepared()
        fake = FakeProvider(mode)
        result, report = self.execute(fake)
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(report["actual_provider_attempts"], 1)
        self.assertFalse(report["interpretation_completed"])
        challenge = cli.read(self.case / "execution/challenge.json")
        self.assertEqual(len(challenge["trials"]), 2)
        self.assertEqual(challenge["trials"][1]["execution"]["status"], "skipped_after_failure")
        self.assertIsNone(challenge["trials"][1]["answer"])
        self.assertFalse((self.case / "execution/interpretation.json").exists())
        return report

    def test_prepare_and_dry_are_provider_free_with_relative_spec_paths_from_other_cwd(self):
        other = self.base / "other cwd"
        other.mkdir()
        previous = Path.cwd()
        try:
            os.chdir(other)
            self.prepared()
            with patch.object(cli, "provider", side_effect=AssertionError("No provider in dry run")):
                result = cli.run(self.case)
        finally:
            os.chdir(previous)
        self.assertEqual(result["calls_permitted"], 0)
        self.assertEqual(cli.read(self.case / "plan.json")["lineage"]["source_case"], str(self.source))

    def test_fresh_process_prepare_dry_never_imports_config_or_sdk(self):
        code = r'''
import builtins, json, socket, sys
real_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'config', 'openai'}:
        raise AssertionError('Forbidden import in prepare/dry: ' + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError('Network forbidden'))
sys.path.insert(0, sys.argv[1])
from tools import run_agent_context_challenge as cli
cli.provider = lambda *a, **k: (_ for _ in ()).throw(AssertionError('Provider initialized'))
assert cli.prepare(sys.argv[2], sys.argv[3])['calls_permitted'] == 0
assert cli.run(sys.argv[3])['calls_permitted'] == 0
assert 'config' not in sys.modules and 'openai' not in sys.modules
print('OFFLINE_PREPARE_DRY_OK')
'''
        result = subprocess.run([sys.executable, "-X", "utf8", "-B", "-c", code, str(ROOT), str(self.spec), str(self.case)],
            cwd=self.base, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OFFLINE_PREPARE_DRY_OK", result.stdout)
        self.assertFalse((self.case / "execution").exists())

    def test_three_actual_injected_calls_preserve_input_boundary_wire_raw_and_originals(self):
        self.prepared()
        fake = FakeProvider()
        result, report = self.execute(fake)
        self.assertEqual(result["execution"]["status"], "completed")
        self.assertEqual([x["step"] for x in fake.calls],
                         ["context_challenge.answer", "context_challenge.answer", "agent_editing.question_set_review"])
        self.assertEqual(report["actual_provider_attempts"], 3)
        for key in ("all_observed_wires_match", "complete_response_audit", "exact_messages_and_raw_match"):
            self.assertTrue(report["ledger"][key])
        self.assertEqual(report["ledger"]["reported_total_tokens"], 30)
        for call in fake.calls[:2]:
            self.assertEqual(call["messages"][0], {"role": "system", "content": ANSWER_SYSTEM})
            payload = json.loads(call["messages"][1]["content"])
            self.assertEqual(set(payload), {"public_question", "public_material", "public_protocol"})
            self.assertEqual(payload["public_protocol"], self.protocol)
            self.assertIn("LATE_C", payload["public_material"])
            self.assertIn("LATE_D", payload["public_material"])
            for forbidden in ("EARLY_ONLY", "PRIVATE_REFERENCE", "PRIVATE_REASON", "PRIVATE_REVIEW", "DESIGN_PURPOSE"):
                self.assertNotIn(forbidden, json.dumps(call["messages"], ensure_ascii=False))
        collection = json.loads(fake.calls[2]["messages"][1]["content"])
        self.assertIn("OFFLINE fixture: the selected evidence", json.dumps(collection, ensure_ascii=False))
        self.assertNotIn('"source_report":', json.dumps(collection, ensure_ascii=False))
        self.assertFalse(report["formal_scoring"])
        self.assertFalse(report["generation_receipt_modified"])
        self.assertTrue(report["interpretation_completed"])
        before = hashes(self.case)
        with self.assertRaises(FileExistsError):
            cli.run(self.case, execute=True)
        self.assertEqual(hashes(self.case), before)

    def test_first_provider_403_stops_all_remaining_calls(self):
        self.assert_stopped_first("provider403")

    def test_first_invalid_answer_preserves_raw_and_stops(self):
        self.assert_stopped_first("invalid_answer")
        self.assertEqual(cli.read(self.case / "execution/returns/001.json"), {"answer": "  "})

    def test_first_record_callback_failure_stops_and_preserves_return(self):
        self.assert_stopped_first("record_finished")
        self.assertTrue((self.case / "execution/returns/001.json").exists())

    def test_first_wrong_wire_stops_before_second_solver(self):
        report = self.assert_stopped_first("wrong_wire")
        self.assertFalse(report["ledger"]["all_observed_wires_match"])

    def test_first_return_differs_from_provider_raw_stops(self):
        report = self.assert_stopped_first("wrong_raw")
        self.assertFalse(report["ledger"]["exact_messages_and_raw_match"])

    def test_broken_trace_keeps_failed_report_and_stops(self):
        report = self.assert_stopped_first("broken_trace")
        self.assertIn("ledger_error", report)

    def test_source_drift_before_execute_rejected_without_provider(self):
        self.prepared()
        frozen = cli.source_hashes()
        frozen[next(iter(frozen))] = "0" * 64
        with patch.object(cli, "source_hashes", return_value=frozen), patch.object(cli, "provider") as provider:
            with self.assertRaisesRegex(ValueError, "source changed"):
                cli.run(self.case, execute=True)
        provider.assert_not_called()
        self.assertFalse((self.case / "execution").exists())

    def test_execution_source_copy_change_after_first_return_latches(self):
        self.prepared()
        def mutate(index, directory):
            if index == 1:
                first = next((directory / "source_snapshot").iterdir())
                first.write_text("OFFLINE tampered temporary execution copy", encoding="utf-8")
        fake = FakeProvider(after_return=mutate)
        result, report = self.execute(fake)
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("Execution source copy changed", json.dumps(report))

    def test_setup_copy_failure_keeps_failed_report_without_provider(self):
        self.prepared()
        original = cli._write_bytes
        def fail_copy(path, blob):
            if Path(path).parent == self.case / "execution/inputs":
                raise OSError("OFFLINE setup copy failure")
            return original(path, blob)
        with patch.object(cli, "_write_bytes", side_effect=fail_copy), patch.object(cli, "provider") as provider:
            result = cli.run(self.case, execute=True)
        provider.assert_not_called()
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertEqual(cli.read(self.case / "execution/report.json")["actual_provider_attempts"], 0)
        with self.assertRaises(FileExistsError):
            cli.run(self.case, execute=True)

    def test_trial_checkpoint_callback_failure_stops_next_request(self):
        self.prepared()
        original = cli.save
        def fail_checkpoint(path, value):
            if Path(path).parent == self.case / "execution/trials":
                raise OSError("OFFLINE checkpoint callback failure")
            return original(path, value)
        fake = FakeProvider()
        with patch.object(cli, "save", side_effect=fail_checkpoint):
            result, report = self.execute(fake)
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertEqual(len(fake.calls), 1)
        self.assertFalse(report["interpretation_completed"])
        self.assertTrue((self.case / "execution/returns/001.json").exists())

    def test_changed_prepared_input_or_reviewed_plan_rejected(self):
        self.prepared()
        before = (self.case / "inputs/snapshot.json").read_bytes()
        (self.case / "inputs/snapshot.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Frozen upstream input"):
            cli.run(self.case)
        (self.case / "inputs/snapshot.json").write_bytes(before)
        plan = cli.read(self.case / "plan.json")
        plan["settings"]["solver"]["max_tokens"] += 1
        write(self.case / "plan.json", plan)
        with self.assertRaisesRegex(ValueError, "reviewed spec|marker"):
            cli.run(self.case)
        self.assertFalse((self.case / "execution").exists())

    def test_incomplete_transport_and_wrong_call_cap_rejected_before_case_creation(self):
        original = cli.read(self.spec)
        cases = []
        legacy = deepcopy(original)
        legacy["transport"] = None
        cases.append(legacy)
        for key in ("http_timeout_seconds", "deadline_seconds", "response_format"):
            value = deepcopy(original)
            value["transport"]["profiles"]["high_json"].pop(key)
            cases.append(value)
        wrong_cap = deepcopy(original)
        wrong_cap["max_provider_attempts"] = 4
        cases.append(wrong_cap)
        for index, value in enumerate(cases):
            with self.subTest(index=index):
                write(self.spec, value)
                with patch.object(cli, "provider") as provider:
                    with self.assertRaises(ValueError):
                        cli.prepare(self.spec, self.case)
                provider.assert_not_called()
                self.assertFalse(self.case.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
