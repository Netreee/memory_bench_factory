"""Offline proposal entry-point tests; fixtures do not measure model quality."""
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

from pipeline.question_proposals import prepare_proposals, propose_questions
from pipeline.semantic_review import prepare_review, review_questions
from eval.provenance import digest
from tools.propose_questions import _new_output, main


CORPUS = {"world": "PRIVATE_WORLD", "sessions": [
    {"session_id": 4, "date": "later", "private": "PRIVATE_SESSION", "docs": [
        {"content": "更正：登记只是计划，分析尚未确认。", "title": "后续更正", "is_filler": True,
         "is_conflict": True, "doc_id": "PRIVATE_DOC_ID", "fact_refs": ["PRIVATE_FACT"]}]},
    {"session_id": 2, "date": "earlier", "docs": [
        {"content": "登记处称客户所属市场为欧洲。", "title": "登记记录", "quality_review": "PRIVATE_REVIEW"}]}]}
SEED = {"case": "SEED_ONLY_FACT", "goal": "核查记录来源与分析确认的区别", "source": {"amount": 123}}
PROTOCOL = "仅按公开材料判断；不能确定时说明缺口。"


def proposal(**changes):
    value = {"question": "能否把登记结论当作分析已确认？说明证据。",
        "reference_proposal": {"answer": "不能；后续更正表明尚未确认。", "rationale": "公开更正限制了登记的含义。"},
        "evidence": [{"doc_id": "d000002", "field": "content", "quote": "分析尚未确认", "role": "support",
                      "explanation": "后续更正对确认状态作出限制。"}],
        "mechanism_target": {"intent": "区分记录与确认", "realization": "需结合登记与后续更正的来源和时间",
                             "uncertainties": ["是否真的需要多步理解仍待检验"]}}
    value.update(changes)
    return value


def response(*rows):
    return {"questions": list(rows) or [proposal()], "limitations": ["候选尚未独立审阅"]}


class Scripted:
    def __init__(self, output):
        self.output, self.calls = output, []

    def __call__(self, step, messages, **kwargs):
        self.calls.append({"step": step, "messages": deepcopy(messages), "params": kwargs})
        if isinstance(self.output, Exception):
            raise self.output
        return deepcopy(self.output)


class ProposalTests(unittest.TestCase):
    def run_batch(self, output=None, **kwargs):
        script = Scripted(response() if output is None else output)
        report = propose_questions(CORPUS, PROTOCOL, "来源理解", seed=SEED, model="offline-proposer",
                                   chat_json=script, **kwargs)
        return report, script

    def test_full_sorted_public_view_and_separate_design_targets(self):
        before = deepcopy(CORPUS)
        report, script = self.run_batch()
        payload = json.loads(script.calls[0]["messages"][1]["content"])
        self.assertEqual(set(payload), {"documents", "public_protocol", "design_targets", "count"})
        self.assertEqual([doc["session"] for doc in payload["documents"]], [2, 4])
        self.assertEqual(len(payload["documents"]), 2)  # includes filler/conflict body
        self.assertNotIn("PRIVATE_", json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("title", payload["documents"][0])
        self.assertEqual(payload["design_targets"]["seed"], SEED)
        self.assertEqual(payload["design_targets"]["authority"], "design_targets_only_not_public_evidence")
        self.assertNotIn("SEED_ONLY_FACT", json.dumps(payload["documents"]))
        self.assertEqual(report["source_map"][1]["source_doc_id"], "PRIVATE_DOC_ID")
        self.assertEqual(CORPUS, before)

    def test_binding_changes_with_visible_inputs_or_targets(self):
        base = prepare_proposals(CORPUS, PROTOCOL, seed=SEED, model="offline")
        titled = prepare_proposals(CORPUS, PROTOCOL, seed=SEED, model="offline", include_titles=True)
        changed_seed = prepare_proposals(CORPUS, PROTOCOL, seed={**SEED, "case": "other"}, model="offline")
        changed_protocol = prepare_proposals(CORPUS, "other", seed=SEED, model="offline")
        self.assertNotEqual(base["binding"]["corpus_hash"], titled["binding"]["corpus_hash"])
        self.assertEqual(titled["documents"][0]["title"], "登记记录")
        self.assertNotEqual(base["binding"]["design_targets_hash"], changed_seed["binding"]["design_targets_hash"])
        self.assertNotEqual(base["binding"]["protocol_hash"], changed_protocol["binding"]["protocol_hash"])
        hidden_changed = deepcopy(CORPUS)
        hidden_changed["world"] = "different private world"
        other = prepare_proposals(hidden_changed, PROTOCOL, seed=SEED, model="offline")
        self.assertEqual(base["binding"], other["binding"])

    def test_exact_batch_stays_pending_and_records_full_single_call(self):
        events = []
        report, script = self.run_batch(response(proposal()), count=1, record=events.append)
        self.assertEqual(len(script.calls), 1)
        self.assertEqual(script.calls[0]["params"]["retries"], 1)
        self.assertTrue(script.calls[0]["params"]["strict_json"])
        self.assertEqual(report["execution"]["status"], "ok")
        self.assertEqual(report["result_scope"], "research_only")
        self.assertEqual(report["publication_effect"], "none")
        self.assertEqual(report["review_state"], "pending_independent_semantic_review")
        self.assertEqual([event["event"] for event in events], ["started", "finished"])
        self.assertEqual(events[0]["messages"], script.calls[0]["messages"])
        self.assertEqual(events[1]["output"], response(proposal()))

    def test_shortfall_and_excess_preserved_without_retry_or_truncation(self):
        for rows, count, status in [([], 3, "shortfall"), ([proposal()], 3, "shortfall"),
                                    ([proposal(), proposal(), proposal()], 1, "excess")]:
            with self.subTest(status=status, n=len(rows)):
                output = {"questions": rows, "limitations": ["实际数量"]}
                report, script = self.run_batch(output, count=count)
                self.assertEqual(len(script.calls), 1)
                self.assertEqual(report["quantity"]["status"], status)
                self.assertEqual(len(report["candidates"]), len(rows))
                self.assertEqual(report["raw_output"], output)

    def test_bad_rows_remain_visible_and_do_not_erase_good_rows(self):
        bad_evidence = proposal(evidence=[{"doc_id": "seed", "field": "amount", "quote": "123", "role": [], "explanation": "bad"}])
        output = response(proposal(), "not an object", {"question": 8}, bad_evidence)
        report, script = self.run_batch(output)
        self.assertEqual(report["execution"]["status"], "invalid_output")
        self.assertEqual(report["raw_output"], output)
        self.assertEqual(len(report["candidates"]), 4)
        self.assertEqual(len(report["questions"]), 2)  # locatable question/reference can still be independently challenged
        self.assertEqual(report["candidates"][1]["raw_proposal"], "not an object")
        self.assertTrue(any(issue.startswith("evidence[0]:citation_location_invalid:")
                            for issue in report["candidates"][3]["format_issues"]))
        self.assertEqual(len(script.calls), 1)

    def test_explicit_field_citation_resolves_only_actual_visible_text(self):
        citation = {"doc_id": "d000002", "field": "content", "location_scope": "field",
                    "role": "support", "explanation": "依据更正判断分析是否已经确认"}
        row = proposal(evidence=[citation])
        report, _ = self.run_batch(response(row), count=1)
        self.assertEqual(report["execution"]["status"], "ok")
        candidate = report["candidates"][0]
        resolved = candidate["resolved_evidence"][0]
        self.assertEqual(resolved["resolved_text"], "更正：登记只是计划，分析尚未确认。")
        self.assertEqual(resolved["locator_source"], "program_resolved_field")
        self.assertEqual(resolved["location_scope"], "field")
        self.assertIsInstance(resolved["document_hash"], str)
        self.assertIsInstance(resolved["field_hash"], str)
        self.assertEqual(candidate["raw_proposal"], row)
        self.assertEqual(report["raw_output"], response(row))
        self.assertNotIn("resolved_evidence", report["questions"][0])
        self.assertNotIn("evidence", report["questions"][0])
        self.assertEqual(candidate["review_state"], "pending_independent_semantic_review")

    def test_legacy_verbatim_quote_stays_strict_and_never_falls_back_to_field(self):
        valid, _ = self.run_batch(response(proposal()), count=1)
        self.assertEqual(valid["candidates"][0]["resolved_evidence"][0]["locator_source"], "model_verbatim_quote")
        bad_quote = {"doc_id": "d000002", "field": "content", "quote": "更正...分析尚未确认",
                     "role": "support", "explanation": "错误的省略号拼接"}
        for citation in (bad_quote, {**bad_quote, "location_scope": "field"},
                         {**bad_quote, "location_scope": "quote"},
                         {**bad_quote, "location_scope": "field", "quote": None}):
            with self.subTest(citation=citation):
                report, _ = self.run_batch(response(proposal(evidence=[citation])), count=1)
                self.assertEqual(report["execution"]["status"], "invalid_output")
                self.assertEqual(report["candidates"][0]["resolved_evidence"], [])
                self.assertTrue(report["candidates"][0]["format_issues"])
                self.assertEqual(len(report["questions"]), 1)  # readable questions still reach independent review

    def test_field_locator_cannot_invent_program_metadata_or_hidden_titles(self):
        citation = {"doc_id": "d000002", "field": "content", "location_scope": "field",
                    "role": "support", "explanation": "来源判断仍需要语义审阅"}
        for reserved in ("locator_source", "resolved_text", "document_hash", "field_hash"):
            report, _ = self.run_batch(response(proposal(evidence=[{**citation, reserved: "forged"}])), count=1)
            self.assertEqual(report["execution"]["status"], "invalid_output")
            self.assertEqual(report["candidates"][0]["resolved_evidence"], [])
        hidden = {**citation, "field": "title"}
        report, _ = self.run_batch(response(proposal(evidence=[hidden])), count=1)
        self.assertEqual(report["execution"]["status"], "invalid_output")
        titled, _ = self.run_batch(response(proposal(evidence=[hidden])), count=1, include_titles=True)
        self.assertEqual(titled["execution"]["status"], "ok")
        self.assertEqual(titled["candidates"][0]["resolved_evidence"][0]["resolved_text"], "后续更正")

    def test_located_but_semantically_wrong_role_is_not_program_verified_truth(self):
        row = proposal(reference_proposal={"answer": "分析已经确认", "rationale": "这个提案有意与公开更正相反"},
            evidence=[{"doc_id": "d000002", "field": "content", "location_scope": "field",
                       "role": "support", "explanation": "错误地把尚未确认解释为支持已经确认"}])
        report, _ = self.run_batch(response(row), count=1)
        self.assertEqual(report["execution"]["status"], "ok")  # only location was verified
        self.assertEqual(report["candidates"][0]["resolved_evidence"][0]["role"], "support")
        self.assertEqual(report["candidates"][0]["review_state"], "pending_independent_semantic_review")
        self.assertNotIn("correct", report["questions"][0])
        self.assertEqual(report["questions"][0]["reference_proposal"], row["reference_proposal"])

    def test_unknown_question_type_and_disputable_reference_are_not_hard_rejected(self):
        row = proposal(question="比较两份材料如何表达责任归属，并提出尚不能判定的解释。",
                       reference_proposal={"answer": "SEED_ONLY_FACT", "rationale": "错误地使用seed作为事实"})
        report, _ = self.run_batch(response(row), count=1)
        self.assertEqual(report["execution"]["status"], "ok")  # wire-valid is not semantically correct
        self.assertEqual(report["questions"][0]["reference_proposal"]["answer"], "SEED_ONLY_FACT")
        self.assertNotIn("line", report["questions"][0])
        self.assertNotIn("capability", report["questions"][0])
        self.assertEqual(report["candidates"][0]["review_state"], "pending_independent_semantic_review")

    def test_missing_and_non_template_references_all_reach_independent_review(self):
        absent = proposal(question="无参考的题目？")
        absent.pop("reference_proposal")
        rows = [absent,
                proposal(question="显式空参考的题目？", reference_proposal=None),
                proposal(question="自然语言参考的题目？", reference_proposal="仍未确认"),
                proposal(question="只有answer的题目？", reference_proposal={"answer": "仍未确认"})]
        for row in rows:
            row["mechanism_target"]["intent"] = "PRIVATE_MECHANISM_TARGET"
        report, script = self.run_batch(response(*rows), count=4)
        self.assertEqual(len(script.calls), 1)
        self.assertEqual(report["execution"]["status"], "invalid_output")
        self.assertEqual(len(report["questions"]), 4)
        self.assertNotIn("reference_proposal", report["questions"][0])
        self.assertIsNone(report["questions"][1]["reference_proposal"])
        for index, candidate in enumerate(report["candidates"]):
            self.assertTrue(candidate["review_input_ready"])
            self.assertIn("reference_proposal_needs_answer_and_text_rationale", candidate["format_issues"])
            self.assertEqual(candidate["raw_proposal"], rows[index])
        self.assertEqual(report["questions"][2]["reference_proposal"], "仍未确认")
        self.assertEqual(report["questions"][3]["reference_proposal"], {"answer": "仍未确认"})
        review = prepare_review(report, CORPUS, PROTOCOL, reviewer_model="offline-reviewer")
        self.assertEqual(len(review["items"]), 4)
        self.assertEqual([item["reference_provided"] for item in review["items"]], [False, True, True, True])
        self.assertEqual(review["items"][2]["reference_proposal"], "仍未确认")
        self.assertEqual(review["items"][3]["reference_proposal"], {"answer": "仍未确认"})
        self.assertNotIn("SEED_ONLY_FACT", json.dumps(review))
        self.assertNotIn("PRIVATE_MECHANISM_TARGET", json.dumps(review))

    def test_independent_review_does_not_receive_seed_or_mechanism_explanation(self):
        row = proposal(mechanism_target={"intent": "PRIVATE_GOAL", "realization": "PRIVATE_MECHANISM", "uncertainties": []})
        report, _ = self.run_batch(response(row))
        review = prepare_review(report, CORPUS, PROTOCOL, reviewer_model="offline-reviewer")
        self.assertEqual(len(review["items"]), 1)
        self.assertNotIn("PRIVATE_GOAL", json.dumps(review))
        self.assertNotIn("PRIVATE_MECHANISM", json.dumps(review))
        self.assertNotIn("SEED_ONLY_FACT", json.dumps(review))
        self.assertEqual(review["items"][0]["review_state"], "pending")

    def test_origin_addresses_exact_batch_content_and_raw_candidate_position(self):
        first, _ = self.run_batch(response(proposal()), count=1)
        repeated, _ = self.run_batch(response(proposal()), count=1)
        other, _ = self.run_batch(response(proposal(question="哪些部分仍未确认？")), count=1)
        origin = first["questions"][0]["origin"]
        self.assertEqual(origin, repeated["questions"][0]["origin"])
        self.assertEqual(first["questions"][0]["qid"], other["questions"][0]["qid"])
        self.assertNotEqual(origin, other["questions"][0]["origin"])
        self.assertEqual(origin["proposal_version"], first["version"])
        self.assertEqual(origin["batch_hash"], digest({"binding": first["binding"], "raw_output": first["raw_output"]}))
        self.assertEqual(origin["output_hash"], digest(first["raw_output"]))
        self.assertEqual(origin["batch_hash"], first["batch_hash"])
        self.assertNotIn("SEED_ONLY_FACT", json.dumps(origin))
        mixed, _ = self.run_batch(response("unusable", proposal()), count=2)
        self.assertEqual(mixed["questions"][0]["origin"]["candidate_index"], 1)
        self.assertEqual(mixed["questions"][0]["origin"]["candidate_id"], "p000002")

    def test_origin_never_enters_blind_reader_or_adjudicator_material(self):
        field_citation = {"doc_id": "d000002", "field": "content", "location_scope": "field",
                          "role": "support", "explanation": "WRITER_EVIDENCE_SENTINEL"}
        proposals, _ = self.run_batch(response(proposal(evidence=[field_citation])), count=1)
        origin = proposals["questions"][0]["origin"]
        payloads = []
        def reader(step, messages, **kwargs):
            payload = json.loads(messages[1]["content"])
            payloads.append(payload)
            serialized = json.dumps(payload)
            self.assertNotIn('"origin"', serialized)
            self.assertNotIn(origin["batch_hash"], serialized)
            self.assertNotIn(origin["output_hash"], serialized)
            self.assertNotIn("SEED_ONLY_FACT", serialized)
            self.assertNotIn("WRITER_EVIDENCE_SENTINEL", serialized)
            self.assertNotIn("program_resolved_field", serialized)
            self.assertNotIn('"resolved_evidence"', serialized)
            common = {"interpretation": "询问分析是否确认", "answerability": "answerable",
                "coverage": {"status": "complete", "scope_conflict": False, "limitations": []},
                "evidence": proposal()["evidence"], "reasoning": "后续记录明确尚未确认。"}
            if step == "semantic_review.blind_read":
                return {**common, "answer": "尚未确认"}
            return {**common, "item_validity": "valid", "reference_status": "supported",
                    "reviewed_answer": "尚未确认", "concerns": []}
        report = review_questions(proposals, CORPUS, PROTOCOL, reviewer_model="offline-reviewer", chat_json=reader)
        self.assertEqual(report["calls_used"], 2)
        self.assertEqual(len(payloads), 2)
        self.assertEqual(report["items"][0]["review_state"], "completed")

    def test_budget_skips_preserve_full_inputs_without_calls(self):
        for kwargs, expected in [({"max_calls": 0}, "call_budget_exhausted"),
                                  ({"max_input_chars": 1}, "input_limit")]:
            report, script = self.run_batch(**kwargs)
            self.assertEqual(script.calls, [])
            self.assertEqual(report["execution"]["status"], expected)
            self.assertEqual(report["records"][0]["event"], "skipped")
            self.assertEqual(len(report["documents"]), 2)
            self.assertIn("SEED_ONLY_FACT", report["messages"][1]["content"])

    def test_errors_and_malformed_outputs_never_trigger_retries(self):
        for output in (RuntimeError("offline failure"), {"__error__": "provider failure"}, "bad JSON object", [],
                       {"questions": [proposal()], "limitations": 9}, {"questions": [], "n": float("nan")}):
            with self.subTest(output=repr(output)):
                report, script = self.run_batch(output)
                self.assertEqual(len(script.calls), 1)
                self.assertIn(report["execution"]["status"], {"model_error", "invalid_output"})
                self.assertEqual(report["records"][-1]["event"], "finished")
                self.assertEqual(report["review_state"], "pending_independent_semantic_review")

    def test_record_failure_stops_before_model_call(self):
        script = Scripted(response())
        with self.assertRaises(OSError):
            propose_questions(CORPUS, PROTOCOL, seed=SEED, model="offline", chat_json=script,
                              record=Mock(side_effect=OSError("audit unavailable")))
        self.assertEqual(script.calls, [])

    def test_invalid_budgets_and_inputs_fail_before_call(self):
        for changes in ({"count": 0}, {"count": True}, {"max_calls": 2}, {"max_calls": True},
                        {"max_tokens": 0}, {"max_input_chars": 0}, {"include_titles": "yes"}):
            script = Scripted(response())
            with self.assertRaises(ValueError):
                propose_questions(CORPUS, PROTOCOL, seed=SEED, model="offline", chat_json=script, **changes)
            self.assertEqual(script.calls, [])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.paths = {}
        for name, value in (("corpus", CORPUS), ("seed", SEED),
                            ("protocol", {"answer_protocol": {"rules": [PROTOCOL]}, "private": "ABOUT_PRIVATE"})):
            path = self.base / f"{name}.json"
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            self.paths[name] = path
        self.output = self.base / "new/report.json"
        self.argv = ["--corpus", str(self.paths["corpus"]), "--seed", str(self.paths["seed"]),
                     "--protocol", str(self.paths["protocol"]), "--output", str(self.output), "--model", "offline"]
        self.addCleanup(self.tmp.cleanup)

    def test_default_prepare_has_no_config_or_legacy_import(self):
        code = '''import sys
class Deny:
    def find_spec(self, name, *args):
        if name == "config" or name.startswith(("pipeline.lines", "pipeline.grounding", "pipeline.phrase", "pipeline.well_posed")):
            raise AssertionError("forbidden import: " + name)
sys.meta_path.insert(0, Deny())
from tools.propose_questions import main
main(sys.argv[1:])
'''
        completed = subprocess.run([sys.executable, "-X", "utf8=0", "-c", code, *self.argv],
                                   cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(report["mode"], "prepared")
        self.assertEqual(report["calls_used"], 0)
        self.assertNotIn("ABOUT_PRIVATE", json.dumps(report["messages"]))
        self.assertFalse(self.output.with_name(self.output.name + ".calls.jsonl").exists())

    def test_execute_real_cli_with_fake_config_preserves_trace_and_proposals(self):
        from llm_trace import emit
        fake = ModuleType("config")
        fake._trace_secrets = lambda: ["MOCK_CREDENTIAL"]
        calls = []
        def chat(messages, **kwargs):
            calls.append((deepcopy(messages), kwargs))
            emit("attempt", secrets=fake._trace_secrets(), request=messages, token="MOCK_CREDENTIAL")
            return {"questions": [proposal()], "limitations": ["MOCK_CREDENTIAL"]}
        fake.chat_json = chat
        with patch.dict(sys.modules, {"config": fake}), redirect_stdout(io.StringIO()):
            self.assertEqual(main([*self.argv, "--execute", "--max-calls", "1", "--count", "3"]), 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]["retries"], 1)
        report = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(report["quantity"]["status"], "shortfall")
        self.assertEqual(report["execution"]["status"], "ok")
        self.assertEqual(report["review_state"], "pending_independent_semantic_review")
        self.assertEqual(report["limitations"], ["[redacted]"])
        traces = [json.loads(line) for line in self.output.with_name(self.output.name + ".calls.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([event["event"] for event in traces], ["started", "finished"])
        attempts = self.output.with_name(self.output.name + ".attempts.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("MOCK_CREDENTIAL", attempts)
        self.assertIn("[redacted]", attempts)

    def test_cli_zero_budget_does_not_load_config(self):
        with patch.dict(sys.modules, {"config": None}), redirect_stdout(io.StringIO()):
            main([*self.argv, "--execute", "--max-calls", "0"])
        report = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(report["mode"], "prepared")
        self.assertEqual(report["calls_used"], 0)

    def test_execute_requires_explicit_budget_and_rejects_larger_budget(self):
        for args in (["--execute"], ["--execute", "--max-calls", "2"]):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main([*self.argv, *args])
        self.assertFalse(self.output.exists())

    def test_cannot_overwrite_input_output_or_sidecars(self):
        before = self.paths["seed"].read_bytes()
        with self.assertRaises(ValueError):
            _new_output(self.paths["seed"], list(self.paths.values()))
        self.assertEqual(self.paths["seed"].read_bytes(), before)
        self.output.parent.mkdir()
        sidecar = self.output.with_name(self.output.name + ".calls.jsonl")
        sidecar.write_text("preserve", encoding="utf-8")
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(self.argv)
        self.assertFalse(self.output.exists())
        self.assertEqual(sidecar.read_text(encoding="utf-8"), "preserve")

    def test_cannot_write_to_historical_runs_including_external_copies(self):
        with self.assertRaises(ValueError):
            _new_output(ROOT / "output/runs/new-proposal/report.json", [])
        old = self.base / "old-run"
        old.mkdir()
        (old / "manifest.json").write_text("{}", encoding="utf-8")
        (old / "02_world.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            _new_output(old / "new/report.json", [])


if __name__ == "__main__":
    unittest.main()
