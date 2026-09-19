"""Offline isolation, completeness and bounded execution tests for materials."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.provenance import digest, load_visible_corpus
from pipeline.material_proposals import prepare_materials, propose_materials
from pipeline.question_proposals import prepare_proposals
from pipeline.semantic_review import prepare_review, visible_documents
from tools.propose_materials import _new_output, main


SEED = {"mechanism": "PRIVATE_SEED_TARGET", "raw_example": {"person": "PRIVATE_SEED_PERSON", "amount": 123}}
INTENT = {"objective": "以虚构工作记录表达收到与实际采用的区别", "notes": ["可以保留未知，不补预定答案"]}
PROTOCOL = "只依据公开资料回答；未记录不自动等于未发生。"


def output():
    return {"corpus": {"sessions": [
        {"session_id": 0, "date": "2025-01-06", "docs": [
            {"title": "TITLE_NOT_VISIBLE", "content": "梁桐致项目组：新报告已归档。是否替换旧依据，请等周五会签。", "private": "PRIVATE_DOC_NOTE"}]},
        {"session_id": 1, "date": "2025-01-10", "docs": [
            {"title": "会签记录", "content": "项目组决定本次继续沿用先前确认的材料。新报告仍需核对报告期间。"}]}]},
        "public_protocol": "只用公开业务记录作答，并说明不能确定之处。",
        "design_notes": {"background": "PRIVATE_AUTHOR_WORLD", "mechanism_realization": "PRIVATE_MECHANISM_EXPLANATION",
                         "uncertainties": ["是否实际测到该机制尚未确认"]},
        "limitations": ["本批只有两篇"]}


class Scripted:
    def __init__(self, value):
        self.value, self.calls = value, []

    def __call__(self, step, messages, **params):
        self.calls.append({"step": step, "messages": deepcopy(messages), "params": params})
        if isinstance(self.value, Exception):
            raise self.value
        return deepcopy(self.value)


class MaterialTests(unittest.TestCase):
    def batch(self, value=None, **kwargs):
        script = Scripted(output() if value is None else value)
        report = propose_materials(SEED, INTENT, model="offline-writer", chat_json=script, **kwargs)
        return report, script

    def test_exact_generator_inputs_hashes_and_fixed_protocol(self):
        report, script = self.batch(public_protocol=PROTOCOL)
        payload = json.loads(script.calls[0]["messages"][1]["content"])
        self.assertEqual(payload["seed"], SEED)
        self.assertEqual(payload["design_intent"], INTENT)
        self.assertEqual(payload["fixed_public_protocol"], PROTOCOL)
        self.assertTrue(payload["fixed_public_protocol_provided"])
        self.assertEqual(report["public_protocol"], PROTOCOL)
        self.assertEqual(report["proposed_public_protocol"], output()["public_protocol"])
        self.assertEqual(report["public_protocol_source"], "caller_fixed")
        self.assertEqual(report["public_binding"]["protocol_hash"], digest(PROTOCOL))
        self.assertEqual(report["binding"]["seed_hash"], digest(SEED))
        self.assertEqual(report["binding"]["messages_hash"], digest(script.calls[0]["messages"]))

    def test_explicit_empty_protocol_is_not_replaced_by_writer(self):
        report, _ = self.batch(public_protocol="")
        self.assertEqual(report["public_protocol"], "")
        self.assertEqual(report["public_protocol_source"], "caller_fixed")
        self.assertTrue(report["binding"]["fixed_public_protocol_provided"])

    def test_no_fixed_protocol_preserves_model_suggestion_as_pending(self):
        report, _ = self.batch()
        self.assertEqual(report["public_protocol"], output()["public_protocol"])
        self.assertEqual(report["public_protocol_source"], "model_proposed_pending_review")
        self.assertEqual(report["review_state"], "pending_independent_material_review")
        self.assertEqual(report["publication_effect"], "none")
        self.assertEqual(report["result_scope"], "research_only")

    def test_model_protocol_wire_readiness_does_not_certify_its_semantic_scope(self):
        value = output()
        value["public_protocol"] = "The answer is approved."
        report, _ = self.batch(value)
        self.assertTrue(report["downstream_ready"])
        self.assertEqual(report["downstream_ready_scope"], "transport_only_not_semantic_approval")
        self.assertEqual(report["protocol_review_state"], "pending_independent_protocol_review")
        self.assertEqual(report["public_protocol"], value["public_protocol"])
        fixed, _ = self.batch(value, public_protocol=PROTOCOL)
        self.assertEqual(fixed["public_protocol"], PROTOCOL)
        self.assertEqual(fixed["protocol_review_state"], "caller_fixed_not_reviewed_here")

    def test_public_projection_excludes_seed_author_notes_private_fields_and_default_titles(self):
        report, _ = self.batch(public_protocol=PROTOCOL)
        docs, _ = visible_documents(report)
        serialized = json.dumps(docs, ensure_ascii=False)
        self.assertEqual(len(docs), 2)
        self.assertNotIn("PRIVATE_", serialized)
        self.assertNotIn("TITLE_NOT_VISIBLE", serialized)
        self.assertNotIn("private", report["corpus"]["sessions"][0]["docs"][0])
        self.assertIn("PRIVATE_DOC_NOTE", json.dumps(report["raw_output"]))
        self.assertIn("PRIVATE_AUTHOR_WORLD", json.dumps(report["design_notes"]))
        question_input = prepare_proposals(report, PROTOCOL, "独立题目设计目标", model="offline-proposer")
        self.assertNotIn("PRIVATE_", json.dumps(question_input["messages"]))
        review_input = prepare_review([{"question": "是否已经改用新报告？"}], report, PROTOCOL,
                                      reviewer_model="offline-reviewer")
        self.assertNotIn("PRIVATE_", json.dumps(review_input))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corpus.json"
            path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(load_visible_corpus(path), [(0, "2025-01-06", docs[0]["content"]),
                                                       (1, "2025-01-10", docs[1]["content"])])

    def test_title_visibility_is_opt_in_not_silently_lost_from_original(self):
        report, _ = self.batch()
        docs, _ = visible_documents(report, include_titles=True)
        self.assertEqual(docs[0]["title"], "TITLE_NOT_VISIBLE")
        self.assertNotEqual(report["projection"]["visible_body_hash"], report["projection"]["visible_with_titles_hash"])

    def test_shortfall_and_excess_are_not_parsing_losses_or_retry_triggers(self):
        for count, expected in [(6, "shortfall"), (1, "excess"), (2, "exact")]:
            report, script = self.batch(count=count)
            self.assertTrue(report["downstream_ready"])
            self.assertEqual(report["quantity"]["status"], expected)
            self.assertEqual(report["quantity"]["returned"], 2)
            self.assertEqual(report["quantity"]["readable_bodies"], 2)
            self.assertEqual(len(script.calls), 1)

    def test_one_bad_document_blocks_whole_scope_without_losing_raw_candidates(self):
        for invalid in ({"content": 42}, {"content": ""}, {"content": "正文仍可读", "title": []}, "bad document"):
            value = output()
            value["corpus"]["sessions"][1]["docs"].append(invalid)
            report, script = self.batch(value)
            self.assertFalse(report["downstream_ready"])
            self.assertIsNone(report["corpus"])
            self.assertEqual(report["corpus_proposal"], value["corpus"])
            self.assertEqual(report["raw_output"], value)
            self.assertEqual(len(report["candidates"]), 3)
            self.assertTrue(report["candidates"][0]["body_readable"])
            self.assertEqual(report["candidates"][2]["raw_document"], invalid)
            self.assertEqual(report["execution"]["status"], "invalid_output")
            self.assertEqual(len(script.calls), 1)

    def test_bad_grouping_or_dates_retains_body_and_never_infers_metadata(self):
        for field, bad in [("date", {}), ("date", None), ("session_id", "not-an-integer"), ("session_id", True)]:
            value = output()
            value["corpus"]["sessions"][0][field] = bad
            report, _ = self.batch(value)
            self.assertFalse(report["downstream_ready"])
            self.assertTrue(report["candidates"][0]["body_readable"])
            self.assertEqual(report["candidates"][0]["raw_session_metadata"][field], bad)
        value = output()
        del value["corpus"]["sessions"][0]["date"]
        report, _ = self.batch(value)
        self.assertFalse(report["downstream_ready"])
        self.assertNotIn("date", report["candidates"][0]["raw_session_metadata"])

    def test_duplicate_groups_never_merge_and_original_order_is_retained(self):
        value = output()
        value["corpus"]["sessions"].reverse()
        report, _ = self.batch(value)
        self.assertEqual([s["session_id"] for s in report["corpus"]["sessions"]], [1, 0])
        self.assertTrue(report["projection"]["adapter_would_reorder_sessions"])
        value["corpus"]["sessions"][1]["session_id"] = 1
        duplicate, _ = self.batch(value)
        self.assertFalse(duplicate["downstream_ready"])
        self.assertEqual(duplicate["corpus_proposal"], value["corpus"])
        self.assertIn("duplicate_session_ids_not_merged", duplicate["execution"]["format_issues"])

    def test_no_lexical_or_fixed_business_status_gate(self):
        value = output()
        value["corpus"]["sessions"][0]["date"] = "周五会议前"
        value["corpus"]["sessions"][0]["docs"][0]["content"] = "尚无结论。编写者未使用保险、采用、已确认等指定标签；解释可能不成立。"
        value["corpus"]["sessions"][1]["docs"][0]["content"] = "【未经核实】这里即使出现某个标签，也不由Python作语义淘汰。"
        report, _ = self.batch(value)
        self.assertTrue(report["downstream_ready"])
        self.assertEqual(report["execution"]["status"], "ok")
        self.assertEqual(report["corpus"]["sessions"][0]["date"], "周五会议前")
        self.assertEqual(report["review_state"], "pending_independent_material_review")

    def test_protocol_and_notes_shape_errors_do_not_rewrite_good_bodies(self):
        value = output()
        value["public_protocol"] = 8
        value["design_notes"] = "bad notes wrapper"
        report, _ = self.batch(value, public_protocol=PROTOCOL)
        self.assertEqual(report["execution"]["status"], "invalid_output")
        self.assertTrue(report["downstream_ready"])
        self.assertEqual(report["public_protocol"], PROTOCOL)
        self.assertEqual(report["proposed_public_protocol"], 8)
        without_fixed, _ = self.batch(value)
        self.assertFalse(without_fixed["downstream_ready"])

    def test_one_call_trace_includes_params_raw_response_and_failure(self):
        events = []
        report, script = self.batch(record=events.append)
        self.assertEqual([event["event"] for event in events], ["started", "finished"])
        self.assertEqual(events[0]["messages"], script.calls[0]["messages"])
        self.assertEqual(events[1]["output"], output())
        self.assertEqual(events[0]["params"]["retries"], 1)
        self.assertTrue(events[0]["params"]["strict_json"])
        for invalid in (RuntimeError("offline failure"), {"__error__": "provider failed"}, "bad shape", {"n": float("nan")}):
            failure, calls = self.batch(invalid)
            self.assertEqual(len(calls.calls), 1)
            self.assertFalse(failure["downstream_ready"])
            self.assertIn(failure["execution"]["status"], {"model_error", "invalid_output"})
            self.assertEqual(failure["records"][-1]["event"], "finished")

    def test_budget_limits_skip_without_truncation_or_calls(self):
        for options, reason in [({"max_calls": 0}, "call_budget_exhausted"), ({"max_input_chars": 1}, "input_limit")]:
            report, script = self.batch(**options)
            self.assertEqual(script.calls, [])
            self.assertEqual(report["execution"]["status"], reason)
            self.assertEqual(report["generator_inputs"]["seed"], SEED)
            self.assertEqual(report["records"][0]["event"], "skipped")
        for options in ({"max_calls": 2}, {"max_calls": True}, {"max_tokens": 0}, {"count": 0}):
            with self.assertRaises(ValueError):
                self.batch(**options)

    def test_audit_failure_prevents_unrecorded_model_call(self):
        script = Scripted(output())
        with self.assertRaises(OSError):
            propose_materials(SEED, INTENT, model="offline", chat_json=script,
                              record=Mock(side_effect=OSError("audit unavailable")))
        self.assertEqual(script.calls, [])

    def test_origins_are_content_bound_and_do_not_expose_seed(self):
        report, _ = self.batch()
        repeated, _ = self.batch()
        changed = output()
        changed["corpus"]["sessions"][0]["docs"][0]["content"] += "另一项说明。"
        other, _ = self.batch(changed)
        self.assertEqual(report["batch_hash"], repeated["batch_hash"])
        self.assertNotEqual(report["batch_hash"], other["batch_hash"])
        self.assertEqual(report["batch_hash"], digest({"binding": report["binding"], "raw_output": report["raw_output"]}))
        self.assertNotIn("PRIVATE_SEED", json.dumps(report["candidates"][0]["origin"]))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.seed, self.intent, self.protocol = [self.base / name for name in ("seed.json", "intent.json", "protocol.json")]
        self.seed.write_text(json.dumps(SEED), encoding="utf-8")
        self.intent.write_text(json.dumps(INTENT, ensure_ascii=False), encoding="utf-8")
        self.protocol.write_text(json.dumps({"public_protocol": PROTOCOL, "private": "PRIVATE_ABOUT"}, ensure_ascii=False), encoding="utf-8")
        self.report = self.base / "new/report.json"
        self.args = ["--seed", str(self.seed), "--intent", str(self.intent), "--protocol", str(self.protocol),
                     "--model", "offline", "--count", "6", "--output", str(self.report)]

    def test_prepare_never_imports_config_or_legacy_generation(self):
        code = '''import sys
class Deny:
    def find_spec(self, name, *args):
        if name == "config" or name.startswith(("pipeline.world_gen", "pipeline.lines", "pipeline.grounding", "pipeline.render")):
            raise AssertionError("forbidden import: " + name)
sys.meta_path.insert(0, Deny())
from tools.propose_materials import main
main(sys.argv[1:])
'''
        child = subprocess.run([sys.executable, "-X", "utf8=0", "-c", code, *self.args], cwd=ROOT,
                               capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(child.returncode, 0, child.stderr)
        report = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertEqual(report["mode"], "prepared")
        self.assertEqual(report["calls_used"], 0)
        self.assertNotIn("PRIVATE_ABOUT", json.dumps(report["messages"]))
        self.assertEqual(report["public_protocol"], PROTOCOL)

    def test_execute_real_cli_with_offline_provider_and_attempt_trace(self):
        from llm_trace import emit
        fake = ModuleType("config")
        fake._trace_secrets = lambda: ["FAKE_CREDENTIAL"]
        calls = []
        def chat(messages, **params):
            calls.append((messages, params))
            emit("attempt", secrets=fake._trace_secrets(), token="FAKE_CREDENTIAL", params=params)
            value = output()
            value["limitations"].append("FAKE_CREDENTIAL")
            return value
        fake.chat_json = chat
        with patch.dict(sys.modules, {"config": fake}), redirect_stdout(io.StringIO()):
            self.assertEqual(main([*self.args, "--execute", "--max-calls", "1"]), 0)
        report = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertTrue(report["downstream_ready"])
        self.assertEqual(report["public_protocol"], PROTOCOL)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]["retries"], 1)
        self.assertEqual(report["limitations"][-1], "[redacted]")
        trace = self.report.with_name(self.report.name + ".calls.jsonl")
        records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["event"] for r in records], ["started", "finished"])
        attempts = self.report.with_name(self.report.name + ".attempts.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("FAKE_CREDENTIAL", attempts)

    def test_explicit_budget_required_and_zero_avoids_config(self):
        for extra in (["--execute"], ["--execute", "--max-calls", "2"]):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main([*self.args, *extra])
        with patch.dict(sys.modules, {"config": None}), redirect_stdout(io.StringIO()):
            main([*self.args, "--execute", "--max-calls", "0"])
        self.assertEqual(json.loads(self.report.read_text(encoding="utf-8"))["calls_used"], 0)

    def test_input_old_output_sidecars_and_historical_runs_cannot_be_overwritten(self):
        before = self.seed.read_bytes()
        with self.assertRaises(ValueError):
            _new_output(self.seed, [self.seed])
        self.assertEqual(self.seed.read_bytes(), before)
        with self.assertRaises(ValueError):
            _new_output(ROOT / "output/runs/new/material.json", [])
        self.report.parent.mkdir()
        sidecar = self.report.with_name(self.report.name + ".calls.jsonl")
        sidecar.write_text("preserve", encoding="utf-8")
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(self.args)
        self.assertFalse(self.report.exists())
        self.assertEqual(sidecar.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
