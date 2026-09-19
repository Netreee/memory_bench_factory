"""Offline checks of series continuity, complete scope and bounded execution."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.provenance import digest
from pipeline.material_series import prepare_material_series, generate_material_series
from pipeline.question_proposals import prepare_proposals
from pipeline.semantic_review import prepare_review, visible_documents


SEED = {"private": "PRIVATE_SEED", "mechanism": "分清来源收到与实际采用"}
INTENT = {"objective": "相互关联的业务记录；有些事情仍可能未知"}
PROTOCOL = "依据提供的公开材料作答，说明不能确定之处。"
SPECS = [
    {"session_id": i * 2, "date": f"2025-01-{6 + i * 7:02d}",
     "intent": f"第{i + 1}期的自然业务沟通", "document_count": 2, "target_chars": 120}
    for i in range(3)
]


def batch(index, *, count=2, content=None):
    spec = SPECS[index]
    return {"corpus": {"sessions": [{"session_id": spec["session_id"], "date": spec["date"],
        "docs": [{"title": f"PRIVATE_TITLE_{index}_{j}",
                  "content": content if content is not None else f"正文{index}-{j}。此前文件仍在核对，负责人计划下周讨论；本次尚不表明已有决定。",
                  "private": f"PRIVATE_DOC_METADATA_{index}"} for j in range(count)]}]},
        "public_protocol": "WRITER_ATTEMPT_TO_REPLACE_FIXED_PROTOCOL",
        "design_notes": {"background": f"PRIVATE_AUTHOR_NOTES_{index}",
            "mechanism_realization": "作者以为没有发生，但这不等于公开证据已证明。",
            "uncertainties": ["该解释可能有误；公开材料保留未知。"]},
        "limitations": [f"第{index}批的原作者局限说明"]}


class Scripted:
    def __init__(self, outputs=None):
        self.outputs = [batch(i) for i in range(3)] if outputs is None else outputs
        self.calls = []

    def __call__(self, step, messages, **params):
        index = len(self.calls)
        self.calls.append({"step": step, "messages": deepcopy(messages), "params": deepcopy(params)})
        value = self.outputs[index]
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)


class MaterialSeriesTests(unittest.TestCase):
    def run_series(self, outputs=None, **overrides):
        provider = Scripted(outputs)
        options = {"model": "offline-writer", "batch_specs": SPECS, "public_protocol": PROTOCOL,
                   "chat_json": provider, "max_calls": 3, "max_input_chars": 100000, "max_tokens": 1024}
        options.update(overrides)
        return generate_material_series(SEED, INTENT, **options), provider

    def test_prepare_is_frozen_and_has_no_execution_or_public_corpus(self):
        seed, specs = deepcopy(SEED), deepcopy(SPECS)
        result = prepare_material_series(seed, INTENT, model="offline-writer", batch_specs=specs, public_protocol=PROTOCOL)
        self.assertEqual(result["mode"], "prepared")
        self.assertEqual(result["calls_used"], 0)
        self.assertEqual(result["binding"]["inputs_hash"], digest(result["series_inputs"]))
        self.assertEqual(result["scale_observations"]["requested_documents"], 6)
        self.assertEqual(result["scale_observations"]["requested_body_chars"], 360)
        seed["private"], specs[0]["intent"] = "mutated", "mutated"
        self.assertEqual(result["series_inputs"]["seed"], SEED)
        self.assertEqual(result["series_inputs"]["batch_specs"], SPECS)
        self.assertIsNone(result["corpus"])
        self.assertFalse(result["downstream_ready"])
        changed_specs = deepcopy(SPECS)
        changed_specs[0]["target_chars"] += 1
        changed = prepare_material_series(SEED, INTENT, model="offline-writer", batch_specs=changed_specs, public_protocol=PROTOCOL)
        self.assertNotEqual(changed["binding"], result["binding"])

    def test_later_batches_receive_every_prior_body_and_complete_fallible_notes(self):
        report, provider = self.run_series()
        for index, call in enumerate(provider.calls):
            payload = json.loads(call["messages"][1]["content"])
            intent = payload["design_intent"]
            self.assertEqual(payload["seed"], SEED)
            self.assertEqual(intent["series_design_intent"], INTENT)
            self.assertEqual(intent["scheduled_batch"], SPECS[index])
            self.assertEqual(intent["previous_public_corpus"]["sessions"], report["corpus"]["sessions"][:index])
            self.assertEqual(len(intent["previous_writer_notes"]), index)
            for prior in range(index):
                note = intent["previous_writer_notes"][prior]
                self.assertEqual(note["design_notes"], batch(prior)["design_notes"])
                self.assertEqual(note["limitations"], batch(prior)["limitations"])
                self.assertIn("fallible", note["authority"])
                self.assertEqual(note["session_id"], SPECS[prior]["session_id"])
            self.assertNotIn(f"PRIVATE_AUTHOR_NOTES_{index}", json.dumps(intent))
            self.assertEqual(payload["fixed_public_protocol"], PROTOCOL)
        self.assertTrue(report["downstream_ready"])
        self.assertEqual(report["execution"]["status"], "completed")
        self.assertEqual(report["calls_used"], 3)
        self.assertEqual([s["session_id"] for s in report["corpus"]["sessions"]], [0, 2, 4])

    def test_public_downstream_view_has_no_private_notes_seed_or_titles(self):
        report, _ = self.run_series()
        docs, _ = visible_documents(report)
        self.assertEqual(len(docs), 6)
        self.assertNotIn("PRIVATE_", json.dumps(docs))
        self.assertEqual(report["public_protocol"], PROTOCOL)
        self.assertEqual(report["result_scope"], "research_only")
        self.assertEqual(report["downstream_ready_scope"], "transport_only_not_semantic_approval")
        self.assertEqual(report["public_binding"]["visible_corpus_hash"], digest(docs))
        proposals = prepare_proposals(report, PROTOCOL, "公开材料中可观察的机制", model="offline")
        review = prepare_review([{"question": "资料是否足以确定已完成？"}], report, PROTOCOL, reviewer_model="offline")
        self.assertNotIn("PRIVATE_", json.dumps(proposals["messages"]))
        self.assertNotIn("PRIVATE_", json.dumps(review))
        self.assertIn("PRIVATE_AUTHOR_NOTES", json.dumps(report["batch_reports"]))

    def test_body_bytes_and_original_raw_documents_are_never_rewritten(self):
        outputs = [batch(i) for i in range(3)]
        outputs[0]["corpus"]["sessions"][0]["docs"][0]["content"] = "  回顾\n第一段\t第二段。 日期2030-01-01是预计计划，不是外层记录日期。  "
        before = deepcopy(outputs)
        report, _ = self.run_series(outputs)
        self.assertEqual(outputs, before)
        for index, session in enumerate(report["corpus"]["sessions"]):
            original = outputs[index]["corpus"]["sessions"][0]
            for found, expected in zip(session["docs"], original["docs"]):
                self.assertEqual(found["content"], expected["content"])
            self.assertEqual(report["batch_reports"][index]["raw_output"], outputs[index])

    def test_shortfall_and_excess_are_observed_without_resampling_or_rejection(self):
        outputs = [batch(0, count=1, content="短。"), batch(1, count=3, content="字" * 100), batch(2)]
        report, provider = self.run_series(outputs)
        self.assertEqual(len(provider.calls), 3)
        self.assertTrue(report["downstream_ready"])
        self.assertFalse(report["scale_observations"]["scale_targets_met"])
        self.assertEqual(report["batch_observations"][0]["document_difference"], -1)
        self.assertEqual(report["batch_observations"][0]["body_char_difference"], 2 - 120)
        self.assertEqual(report["batch_observations"][1]["document_difference"], 1)
        self.assertEqual(report["scale_observations"]["public_body_chars"],
                         sum(len(d["content"]) for s in report["corpus"]["sessions"] for d in s["docs"]))

    def test_bad_second_document_stops_whole_series_preserving_all_raw(self):
        outputs = [batch(i) for i in range(3)]
        outputs[1]["corpus"]["sessions"][0]["docs"][1]["content"] = None
        report, provider = self.run_series(outputs)
        self.assertEqual(len(provider.calls), 2)
        self.assertFalse(report["downstream_ready"])
        self.assertIsNone(report["corpus"])
        self.assertEqual(len(report["partial_corpus"]["sessions"]), 1)
        self.assertEqual(len(report["batch_reports"][1]["candidates"]), 2)
        self.assertEqual(report["batch_reports"][1]["raw_output"], outputs[1])
        self.assertEqual(report["execution"]["status"], "stopped")
        self.assertEqual(report["execution"]["batch_index"], 1)
        with self.assertRaises(ValueError):
            visible_documents(report)

    def test_schedule_mismatch_stops_without_renumbering_or_merging(self):
        for field, bad in (("session_id", 0), ("session_id", "2"), ("session_id", True),
                           ("date", "2025-01-14"), ("date", "回顾上期")):
            with self.subTest(field=field, bad=bad):
                outputs = [batch(i) for i in range(3)]
                outputs[1]["corpus"]["sessions"][0][field] = bad
                report, provider = self.run_series(outputs)
                self.assertEqual(len(provider.calls), 2)
                self.assertIsNone(report["corpus"])
                self.assertEqual(report["batch_reports"][1]["raw_output"], outputs[1])
                self.assertEqual(len(report["partial_corpus"]["sessions"]), 1)
        outputs = [batch(i) for i in range(3)]
        outputs[1]["corpus"]["sessions"].append({"session_id": 99, "date": "2025-01-13", "docs": []})
        report, _ = self.run_series(outputs)
        self.assertIn("batch_must_have_exactly_one_scheduled_session", report["execution"]["scope_issues"])

    def test_malformed_notes_or_protocol_proposal_stop_without_silent_continuation(self):
        for field, invalid in (("design_notes", "not an object"), ("public_protocol", 42)):
            outputs = [batch(i) for i in range(3)]
            outputs[1][field] = invalid
            report, provider = self.run_series(outputs)
            self.assertEqual(len(provider.calls), 2)
            self.assertIsNone(report["corpus"])
            self.assertEqual(report["public_protocol"], PROTOCOL)
            self.assertEqual(report["batch_reports"][1]["raw_output"], outputs[1])

    def test_model_errors_are_recorded_and_stop_before_third_call(self):
        for invalid in (RuntimeError("offline failure"), {"__error__": "provider failed"}, "not JSON object"):
            report, provider = self.run_series([batch(0), invalid, batch(2)])
            self.assertEqual(len(provider.calls), 2)
            self.assertEqual(report["calls_used"], 2)
            self.assertIsNone(report["corpus"])
            self.assertIn(report["execution"]["reason"], ("model_error", "invalid_output"))
            self.assertEqual(report["records"][-1]["event"], "finished")

    def test_call_budget_stops_and_never_claims_the_prefix_is_the_full_case(self):
        for budget in (0, 1):
            report, provider = self.run_series(max_calls=budget)
            self.assertEqual(len(provider.calls), budget)
            self.assertEqual(report["calls_used"], budget)
            self.assertEqual(report["execution"]["reason"], "call_budget_exhausted")
            self.assertEqual(len(report["partial_corpus"]["sessions"]), budget)
            self.assertIsNone(report["corpus"])
            self.assertFalse(report["downstream_ready"])
            self.assertEqual(report["records"][-1]["event"], "skipped")

    def test_context_limit_keeps_complete_prior_text_and_does_not_call_or_truncate(self):
        outputs = [batch(0, content="完整前文" * 1000), batch(1), batch(2)]
        reference, _ = self.run_series(outputs)
        first_chars = reference["batch_reports"][0]["input_chars"]
        report, provider = self.run_series(outputs, max_input_chars=first_chars + 100)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(report["execution"]["reason"], "input_limit")
        skipped = report["batch_reports"][1]
        self.assertEqual(skipped["generator_inputs"]["design_intent"]["previous_public_corpus"], report["partial_corpus"])
        self.assertGreater(skipped["input_chars"], first_chars + 100)
        self.assertIsNone(report["corpus"])

    def test_checkpoints_and_audit_callbacks_cannot_mutate_live_series(self):
        checkpoints, events = [], []
        def checkpoint(value):
            checkpoints.append(deepcopy(value))
            value["series_report"]["partial_corpus"]["sessions"].clear()
            value["batch_report"]["design_notes"].clear()
        def record(value):
            events.append(deepcopy(value))
            value["messages"] = "mutated callback data"
        report, provider = self.run_series(on_batch=checkpoint, record=record)
        self.assertEqual(len(checkpoints), 3)
        self.assertEqual([c["batch_index"] for c in checkpoints], [0, 1, 2])
        self.assertEqual(checkpoints[0]["series_report"]["execution"]["status"], "in_progress")
        self.assertEqual(checkpoints[-1]["series_report"], report)
        self.assertEqual(len(report["corpus"]["sessions"]), 3)
        self.assertEqual(len(events), 6)
        self.assertEqual(events[0]["messages"], provider.calls[0]["messages"])
        self.assertEqual(report["records"][0]["messages"], provider.calls[0]["messages"])
        self.assertTrue(all(c["params"]["retries"] == 1 for c in provider.calls))

    def test_callback_failure_prevents_any_further_model_call(self):
        provider = Scripted()
        def fail(_):
            raise OSError("checkpoint unavailable")
        options = dict(model="offline-writer", batch_specs=SPECS, public_protocol=PROTOCOL,
                       chat_json=provider, max_calls=3, max_input_chars=100000, max_tokens=1024)
        with self.assertRaises(OSError):
            generate_material_series(SEED, INTENT, on_batch=fail, **options)
        self.assertEqual(len(provider.calls), 1)
        provider.calls.clear()
        with self.assertRaises(OSError):
            generate_material_series(SEED, INTENT, record=fail, **options)
        self.assertEqual(provider.calls, [])

    def test_no_semantic_business_gate_even_when_body_assertions_are_disputed(self):
        report, _ = self.run_series([batch(i, content="2028年才可能发生；之前有人说不可能发生。此处没有固定状态枚举，业务推断需要另行审阅。") for i in range(3)])
        self.assertTrue(report["downstream_ready"])
        self.assertEqual(report["review_state"], "pending_independent_material_review")

    def test_invalid_schedule_and_budgets_are_rejected_before_network(self):
        invalid_specs = [[], [None]]
        for field, value in (("session_id", True), ("date", "2025-02-30"), ("date", "20250106"),
                             ("intent", " "), ("document_count", 0), ("target_chars", True)):
            specs = deepcopy(SPECS)
            specs[0][field] = value
            invalid_specs.append(specs)
        for field, value in (("session_id", 0), ("date", "2025-01-01")):
            specs = deepcopy(SPECS)
            specs[1][field] = value
            invalid_specs.append(specs)
        for specs in invalid_specs:
            with self.subTest(specs=specs), self.assertRaises(ValueError):
                self.run_series(batch_specs=specs)
        for overrides in ({"max_calls": True}, {"max_input_chars": 0}, {"max_tokens": 0}, {"public_protocol": None}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.run_series(**overrides)

    def test_import_and_prepare_do_not_load_configuration_or_old_factory(self):
        code = '''import sys
class Deny:
    def find_spec(self, name, *args):
        if name == "config" or name.startswith(("pipeline.world_gen", "pipeline.central_office", "pipeline.render", "pipeline.lines")):
            raise AssertionError("forbidden import: " + name)
sys.meta_path.insert(0, Deny())
from pipeline.material_series import prepare_material_series
result = prepare_material_series({}, "intent", model="offline", batch_specs=[dict(session_id=0, date="2025-01-06", intent="period", document_count=1, target_chars=10)], public_protocol="")
assert result["calls_used"] == 0 and result["public_protocol"] == ""
'''
        child = subprocess.run([sys.executable, "-X", "utf8=0", "-c", code], cwd=ROOT,
                               capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(child.returncode, 0, child.stderr)


if __name__ == "__main__":
    unittest.main()
