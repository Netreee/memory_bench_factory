"""Offline boundaries for serial question proposals; not model-quality tests."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.question_series import propose_question_series
from pipeline.semantic_review import prepare_review, review_questions


CORPUS = {"world": "PRIVATE_WORLD", "sessions": [
    {"session_id": 4, "date": "later", "docs": [
        {"title": "PRIVATE_TITLE", "content": "后续记录仅说明甲公司完成复核。",
         "is_filler": True, "fact_refs": ["PRIVATE_FACT"]}]},
    {"session_id": 1, "date": "earlier", "docs": [
        {"content": "乙公司收到新资料，是否正式采用未记录。", "doc_id": "PRIVATE_ID"}]}]}
SEED = {"goal": "SEED_PRIVATE"}
INTENT = "INTENT_PRIVATE"
PROTOCOL = "只依据给定资料说明结论与不能确定的范围。"
SPECS = [{"focus": f"FOCUS_PRIVATE_{i}", "count": 6} for i in range(4)]


def candidate(question="发生了哪些可以确认的变化？", **changes):
    result = {"question": question,
        "reference_proposal": {"answer": "不能确认乙公司已正式采用。", "rationale": "仅记录接收。"},
        "evidence": [{"doc_id": "d000001", "field": "content", "location_scope": "field",
                      "role": "support", "explanation": "WRITER_EVIDENCE_PRIVATE"}],
        "mechanism_target": {"intent": "MECHANISM_PRIVATE", "realization": "有接收但缺采用记录",
                             "uncertainties": ["参考仍待审阅"]}}
    result.update(changes)
    return result


def output(*rows):
    return {"questions": list(rows), "limitations": []}


class Script:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, step, messages, **params):
        index = len(self.calls)
        self.calls.append({"step": step, "messages": deepcopy(messages), "params": deepcopy(params)})
        value = self.outputs[index]
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)


class SeriesTests(unittest.TestCase):
    def run_series(self, outputs, **changes):
        script = Script(outputs)
        kwargs = dict(model="offline-author", batch_specs=SPECS, chat_json=script,
                      max_calls=4, max_input_chars=200000, max_tokens=4096)
        kwargs.update(changes)
        report = propose_question_series(SEED, INTENT, CORPUS, PROTOCOL, **kwargs)
        return report, script

    def test_four_batches_six_questions_full_history_and_stable_unique_origins(self):
        outputs = [output(*(candidate(f"批{i}题{j}，公开资料如何解释？") for j in range(6)))
                   for i in range(4)]
        report, script = self.run_series(outputs)
        self.assertEqual(len(script.calls), 4)
        self.assertEqual(report["calls_used"], 4)
        self.assertEqual(len(report["questions"]), 24)
        self.assertEqual(len({q["qid"] for q in report["questions"]}), 24)
        self.assertEqual(report["quantity"]["status"], "exact")
        self.assertEqual(report["execution"]["status"], "ok")
        for index, call in enumerate(script.calls):
            payload = json.loads(call["messages"][1]["content"])
            context = payload["design_targets"]["intent"]["series_context"]
            expected = [{"qid": q["qid"], "question": q["question"]}
                        for q in report["questions"][:6 * index]]
            self.assertEqual(context["previous_questions"], expected)
            self.assertEqual(context["focus"], SPECS[index]["focus"])
            self.assertEqual(payload["documents"], report["documents"])
            self.assertEqual(payload["public_protocol"], PROTOCOL)
            self.assertEqual(payload["count"], 6)
            self.assertEqual(call["params"]["retries"], 1)
            self.assertEqual([d["session"] for d in payload["documents"]], [1, 4])
            self.assertNotIn("PRIVATE_WORLD", json.dumps(payload))
            self.assertNotIn("PRIVATE_TITLE", json.dumps(payload))
            self.assertNotIn("PRIVATE_FACT", json.dumps(payload))
            self.assertNotIn("reference_proposal", json.dumps(context["previous_questions"]))
        for q in report["questions"]:
            batch = report["batch_reports"][q["origin"]["series"]["batch_index"]]
            original = batch["questions"][q["origin"]["candidate_index"]]
            for key, value in original["origin"].items():
                self.assertEqual(q["origin"][key], value)
            self.assertEqual(q["origin"]["series"]["source_qid"], original["qid"])
        repeated, _ = self.run_series(outputs)
        self.assertEqual(report["questions"], repeated["questions"])
        self.assertEqual(report["series_output_hash"], repeated["series_output_hash"])

    def test_duplicates_are_all_kept_with_distinct_qids_and_not_normalized(self):
        rows = [output(candidate("相同题？"), candidate("相同题？", reference_proposal="另一个可质疑提案")),
                output(candidate("相同题？"), candidate("相同题？ "))]
        report, script = self.run_series(rows, batch_specs=SPECS[:2])
        self.assertEqual(len(report["questions"]), 4)
        self.assertEqual(len({q["qid"] for q in report["questions"]}), 4)
        self.assertEqual(len(report["duplicates"]["groups"]), 1)
        self.assertEqual(len(report["duplicates"]["groups"][0]["qids"]), 3)
        self.assertEqual(report["duplicates"]["semantic_duplicate_review"], "pending")
        prior = json.loads(script.calls[1]["messages"][1]["content"])["design_targets"]["intent"]["series_context"]["previous_questions"]
        self.assertEqual(len(prior), 2)  # duplicates are not hidden from the next author

    def test_model_provided_identity_does_not_override_source_position(self):
        raw = output(candidate(qid="forged", origin={"batch_hash": "forged"}),
                     candidate("另一个？", qid="forged"))
        report, _ = self.run_series([raw], batch_specs=SPECS[:1])
        self.assertEqual([q["qid"] for q in report["questions"]], ["s001p000001", "s001p000002"])
        self.assertEqual(report["candidates"][0]["raw_proposal"]["origin"], {"batch_hash": "forged"})
        self.assertNotEqual(report["questions"][0]["origin"]["batch_hash"], "forged")

    def test_frozen_binding_changes_with_public_material_and_protocol(self):
        def generate(corpus, protocol):
            return propose_question_series(SEED, INTENT, corpus, protocol, model="offline",
                batch_specs=SPECS[:1], chat_json=Script([]), max_calls=0,
                max_input_chars=200000, max_tokens=4096)
        baseline = generate(CORPUS, PROTOCOL)
        changed = deepcopy(CORPUS)
        changed["sessions"][0]["docs"][0]["content"] += " 新增公开更正。"
        material = generate(changed, PROTOCOL)
        protocol = generate(CORPUS, PROTOCOL + " 新约定。")
        self.assertNotEqual(baseline["binding_hash"], material["binding_hash"])
        self.assertNotEqual(baseline["binding"]["corpus_hash"], material["binding"]["corpus_hash"])
        self.assertNotEqual(baseline["binding_hash"], protocol["binding_hash"])
        self.assertNotEqual(baseline["binding"]["protocol_hash"], protocol["binding"]["protocol_hash"])

    def test_bad_format_missing_reference_null_and_scalar_are_preserved(self):
        absent = candidate("无参考？")
        absent.pop("reference_proposal")
        rows = [absent, candidate("空参考？", reference_proposal=None),
                candidate("标量参考？", reference_proposal=7),
                candidate("错误引文仍可审？", evidence=[{"doc_id": "missing"}])]
        report, _ = self.run_series([output(*rows)], batch_specs=SPECS[:1])
        self.assertEqual(report["execution"]["status"], "invalid_output")
        self.assertEqual(len(report["questions"]), 4)
        self.assertNotIn("reference_proposal", report["questions"][0])
        self.assertIsNone(report["questions"][1]["reference_proposal"])
        self.assertEqual(report["questions"][2]["reference_proposal"], 7)
        review = prepare_review(report, CORPUS, PROTOCOL, reviewer_model="offline-reviewer")
        self.assertEqual([q["reference_provided"] for q in review["items"]], [False, True, True, True])

    def test_readable_string_recovery_does_not_rewrite_base_report(self):
        raw = output("自然语言问题仍应保留？", 17, candidate("字典问题？"))
        report, _ = self.run_series([raw], batch_specs=SPECS[:1])
        batch = report["batch_reports"][0]
        self.assertEqual(batch["raw_output"], raw)
        self.assertEqual(len(batch["questions"]), 1)
        self.assertEqual(len(report["questions"]), 2)
        self.assertEqual([q["qid"] for q in report["questions"]], ["s001p000001", "s001p000003"])
        recovered = report["questions"][0]
        self.assertEqual(recovered["origin"]["series"]["transfer"], "readable_candidate_recovery")
        self.assertEqual(recovered["origin"]["series"]["source_path"], "/questions/0")
        self.assertIsNone(recovered["origin"]["series"]["source_qid"])
        self.assertIn("series_transfer:string_candidate_not_object", report["candidates"][0]["format_issues"])
        self.assertEqual(report["quantity"]["unreadable_candidates"], 1)

    def test_bad_explicit_containers_recover_without_guessing_other_text(self):
        for raw, count, source_path in [([candidate()], 1, "/0"),
                ({"questions": candidate()}, 1, "/questions"),
                ({"questions": "这个题面可读？"}, 1, "/questions"),
                ({"explanation": "不要把这段随意解释当作题目"}, 0, None)]:
            with self.subTest(raw=raw):
                report, _ = self.run_series([raw], batch_specs=SPECS[:1])
                self.assertEqual(len(report["questions"]), count)
                self.assertEqual(report["batch_reports"][0]["raw_output"], raw)
                self.assertEqual(report["execution"]["status"], "invalid_output")
                if count:
                    self.assertEqual(report["questions"][0]["origin"]["series"]["source_path"], source_path)
                    self.assertTrue(report["candidates"][0]["format_issues"])

    def test_shortfall_excess_and_unreadable_denominators_are_explicit(self):
        report, _ = self.run_series([output(), output(*(candidate(str(i)) for i in range(8)))],
                                    batch_specs=[{"focus": "第一批", "count": 2}, {"focus": "第二批", "count": 3}])
        self.assertEqual(report["execution"]["status"], "ok")
        self.assertEqual([x["status"] for x in report["batch_quantities"]], ["shortfall", "excess"])
        self.assertEqual(report["quantity"]["requested"], 5)
        self.assertEqual(report["quantity"]["readable_questions"], 8)
        self.assertEqual(report["quantity"]["difference"], 3)

    def test_global_budget_is_not_per_batch_and_skipped_inputs_are_complete(self):
        report, script = self.run_series([output(candidate())], max_calls=1)
        self.assertEqual(len(script.calls), 1)
        self.assertEqual(report["calls_used"], 1)
        self.assertEqual(len(report["batch_reports"]), 4)
        self.assertEqual([b["execution"]["status"] for b in report["batch_reports"]],
                         ["ok", "call_budget_exhausted", "call_budget_exhausted", "call_budget_exhausted"])
        self.assertEqual(report["execution"]["status"], "incomplete")
        for batch in report["batch_reports"][1:]:
            payload = json.loads(batch["messages"][1]["content"])
            self.assertEqual(len(payload["documents"]), 2)
            self.assertEqual(len(payload["design_targets"]["intent"]["series_context"]["previous_questions"]), 1)
        zero, script = self.run_series([], max_calls=0)
        self.assertEqual(script.calls, [])
        self.assertEqual(zero["quantity"]["requested"], 24)
        self.assertEqual(zero["quantity"]["readable_questions"], 0)

    def test_full_previous_questions_cannot_be_truncated_to_fit_input_limit(self):
        rows = [output(candidate("很长的题面" * 200)), output(candidate("后续题？"))]
        roomy, _ = self.run_series(rows, batch_specs=SPECS[:2])
        first_size = roomy["batch_reports"][0]["input_manifest"]["input_chars"]
        self.assertGreater(roomy["batch_reports"][1]["input_manifest"]["input_chars"], first_size)
        limited, script = self.run_series(rows, batch_specs=SPECS[:2], max_input_chars=first_size)
        self.assertEqual(len(script.calls), 1)
        self.assertEqual(limited["batch_reports"][1]["execution"]["status"], "input_limit")
        payload = json.loads(limited["batch_reports"][1]["messages"][1]["content"])
        self.assertEqual(payload["design_targets"]["intent"]["series_context"]["previous_questions"][0]["question"], "很长的题面" * 200)

    def test_provider_failure_is_recorded_and_later_batch_can_still_propose(self):
        report, script = self.run_series([RuntimeError("offline failed"), output(candidate("后批仍独立出题？"))],
                                         batch_specs=SPECS[:2], max_calls=2)
        self.assertEqual(len(script.calls), 2)
        self.assertEqual(report["batch_reports"][0]["execution"]["error_type"], "RuntimeError")
        self.assertEqual(report["questions"][0]["qid"], "s002p000001")
        self.assertEqual(report["quantity"]["status"], "shortfall")
        self.assertEqual(report["execution"]["status"], "incomplete")

    def test_record_and_checkpoint_receive_copies_and_full_raw_inputs(self):
        events, snapshots = [], []
        def record(event):
            events.append(deepcopy(event))
            event.clear()
        def checkpoint(snapshot):
            snapshots.append(deepcopy(snapshot))
            snapshot["batch_report"]["raw_output"] = "caller poison"
            snapshot["series_binding"].clear()
        report, _ = self.run_series([output(candidate()), output(candidate("后批？"))],
                                    batch_specs=SPECS[:2], record=record, on_batch=checkpoint)
        self.assertEqual(events, report["records"])
        self.assertEqual([s["batch_index"] for s in snapshots], [0, 1])
        self.assertEqual(snapshots[0]["batch_report"]["raw_output"], output(candidate()))
        self.assertNotEqual(report["batch_reports"][0]["raw_output"], "caller poison")
        self.assertEqual(events[0]["frozen_inputs"]["seed"], SEED)
        self.assertTrue(report["binding"])

    def test_callback_failures_propagate_without_more_provider_calls(self):
        calls = Script([output(candidate()), output(candidate())])
        common = dict(model="offline", batch_specs=SPECS[:2], chat_json=calls,
                      max_calls=2, max_input_chars=200000, max_tokens=4096)
        def fail(value):
            raise OSError("checkpoint unavailable")
        with self.assertRaises(OSError):
            propose_question_series(SEED, INTENT, CORPUS, PROTOCOL, record=fail, **common)
        self.assertEqual(calls.calls, [])
        with self.assertRaises(OSError):
            propose_question_series(SEED, INTENT, CORPUS, PROTOCOL, on_batch=fail, **common)
        self.assertEqual(len(calls.calls), 1)

    def test_caller_input_mutation_does_not_change_later_batches(self):
        corpus, seed, specs = deepcopy(CORPUS), deepcopy(SEED), deepcopy(SPECS[:2])
        script = Script([output(candidate()), output(candidate())])
        def checkpoint(snapshot):
            corpus["sessions"] = []
            seed["goal"] = "mutated"
            specs[1]["focus"] = "mutated"
        report = propose_question_series(seed, INTENT, corpus, PROTOCOL, model="offline",
            batch_specs=specs, chat_json=script, max_calls=2, max_input_chars=200000,
            max_tokens=4096, on_batch=checkpoint)
        later = json.loads(script.calls[1]["messages"][1]["content"])
        self.assertEqual(later["design_targets"]["seed"], SEED)
        self.assertEqual(len(later["documents"]), 2)
        self.assertEqual(later["design_targets"]["intent"]["series_context"]["focus"], SPECS[1]["focus"])
        self.assertEqual(report["frozen_inputs"]["corpus"], CORPUS)

    def test_actual_independent_review_payloads_exclude_author_only_context(self):
        report, _ = self.run_series([output(candidate("PREVIOUS_QUESTION_PRIVATE？")), output(candidate())],
                                    batch_specs=SPECS[:2])
        payloads = []
        def reader(step, messages, **params):
            payload = json.loads(messages[1]["content"])
            payloads.append(payload)
            serialized = json.dumps(payload)
            for sentinel in ["SEED_PRIVATE", "INTENT_PRIVATE", "FOCUS_PRIVATE", "MECHANISM_PRIVATE",
                             "WRITER_EVIDENCE_PRIVATE", "PREVIOUS_QUESTION_PRIVATE", "PRIVATE_WORLD"]:
                self.assertNotIn(sentinel, serialized)
            self.assertNotIn("origin", payload)
            common = {"interpretation": "依材料说明能确认的变化", "answerability": "answerable",
                "coverage": {"status": "complete", "scope_conflict": False, "limitations": []},
                "evidence": [], "reasoning": "记录仅支持有限结论"}
            if step.endswith("blind_read"):
                self.assertNotIn("reference_proposal", payload)
                return {**common, "answer": "乙公司的采用仍不能确认"}
            return {**common, "item_validity": "valid", "reference_status": "supported",
                    "reviewed_answer": "乙公司的采用仍不能确认", "concerns": []}
        reviewed = review_questions([report["questions"][-1]], CORPUS, PROTOCOL,
            reviewer_model="offline-reviewer", chat_json=reader, max_calls=2)
        self.assertEqual(len(payloads), 2)
        self.assertEqual(reviewed["items"][0]["review_state"], "completed")

    def test_invalid_specs_and_budgets_fail_before_any_call(self):
        cases = [{"batch_specs": []}, {"batch_specs": [{"focus": "", "count": 6}]},
                 {"batch_specs": [{"focus": "合法", "count": True}]}, {"batch_specs": ["bad"]},
                 {"max_calls": True}, {"max_calls": -1}, {"max_input_chars": 0}, {"max_tokens": 0}]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_series([], **changes)

    def test_import_has_no_provider_or_legacy_gate_dependencies(self):
        code = '''import sys
class Deny:
    def find_spec(self, name, *args):
        if name == "config" or name.startswith(("pipeline.lines", "pipeline.grounding", "pipeline.well_posed", "pipeline.phrase", "pipeline.closed_loop")):
            raise AssertionError("forbidden import: " + name)
sys.meta_path.insert(0, Deny())
import pipeline.question_series
'''
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
