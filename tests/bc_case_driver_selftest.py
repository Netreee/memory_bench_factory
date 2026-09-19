"""Offline driver integration checks; scripted semantics are not quality evidence."""
from __future__ import annotations

import builtins
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_bc_case as driver
from pipeline.semantic_review import prepare_review


PROTOCOL = "仅按公开材料作答；记录不完整时允许说明未知。"
PLAN = {
    "result_scope": "research_only", "material_model": "offline-writer",
    "question_model": "offline-proposer", "reader_model": "offline-reader",
    "reviewer_model": "offline-reviewer", "material_max_tokens": 1024,
    "question_max_tokens": 1024, "review_max_tokens": 1024,
    "max_input_chars": 100000,
    "budgets": {"material_calls": 2, "question_calls": 2, "review_calls": 8},
    "material_batches": [
        {"session_id": i, "date": f"2025-01-{i + 1:02d}", "intent": "自然业务记录",
         "document_count": 1, "target_chars": 10} for i in range(2)],
    "question_batches": [{"focus": "开放候选", "count": 2} for _ in range(2)],
}


class OfflineProvider:
    def __init__(self, *, material_failure=None, review_failure=None,
                 malformed_candidate=False):
        self.material_failure = material_failure
        self.review_failure = review_failure
        self.malformed_candidate = malformed_candidate
        self.calls = []
        self.events = []
        self.lock = threading.Lock()

    def factory(self, directory):
        def record(event):
            with self.lock:
                self.events.append({"directory": str(directory), **deepcopy(event)})
        return self.call, record, deepcopy

    def call(self, step, messages, **params):
        payload = json.loads(messages[1]["content"])
        with self.lock:
            self.calls.append({"step": step, "messages": deepcopy(messages),
                               "params": deepcopy(params)})
        if params["retries"] != 1:
            raise AssertionError("Unexpected implicit retry budget")
        if step == "material_proposals.batch":
            spec = payload["design_intent"]["scheduled_batch"]
            if self.material_failure == spec["session_id"]:
                raise TimeoutError("offline material timeout")
            return {"corpus": {"sessions": [{"session_id": spec["session_id"],
                "date": spec["date"], "docs": [{"title": "PRIVATE_TITLE",
                "content": f"第{spec['session_id']}期收到新材料，尚未决定是否采用。",
                "private": "PRIVATE_DOCUMENT_METADATA"}]}]},
                "public_protocol": "PRIVATE_WRITER_REPLACEMENT_PROTOCOL",
                "design_notes": {"private": "PRIVATE_WRITER_NOTES"}, "limitations": []}
        if step == "question_proposals.batch":
            i = payload["design_targets"]["intent"]["series_context"]["batch_index"]
            rows = [{"question": f"候选{i}-{j}：是否已采用？",
                     "reference_proposal": {"answer": "PRIVATE_REFERENCE", "rationale": "参考仍可推翻"},
                     "evidence": [], "mechanism_target": {"intent": "区分收到与采用"}}
                    for j in range(2)]
            if self.malformed_candidate and i == 1:
                rows[1] = {"question": 42, "reference_proposal": "unreadable"}
            return {"questions": rows, "limitations": []}
        if step == "semantic_review.blind_read" and payload["question"] == self.review_failure:
            raise TimeoutError("offline reader timeout")
        coverage = {"status": "complete", "scope_conflict": False,
                    "inspected_doc_ids": [], "limitations": []}
        evidence = [{"doc_id": "d000001", "field": "content", "location_scope": "field",
                     "role": "support", "explanation": "定位原文，语义为离线脚本设定。"}]
        if step == "semantic_review.blind_read":
            return {"interpretation": payload["question"], "answer": "无法确定",
                    "major_requirements": ["判断材料能否确定所问事实"],
                    "answerability": "unanswerable", "coverage": coverage,
                    "evidence": evidence, "reasoning": "仅有收到记录。"}
        if step == "semantic_review.adjudicate":
            return {"item_validity": "valid", "answerability": "unanswerable",
                    "reference_status": "contradicted", "reviewed_answer": "无法确定",
                    "reviewed_rationale": "仅有收到记录。", "major_requirements": ["判断材料能否确定所问事实"],
                    "original_answer_review": [{"requirement": "所问事实", "assessment": "有误", "explanation": "脚本判断"}],
                    "original_rationale_review": {"status": "reviewed", "claims": [], "limitations": []},
                    "review_findings": {"substantive_defects": ["原参考不能成立"], "acceptable_brevity": [], "editorial_suggestions": []},
                    "interpretation": payload["question"], "coverage": coverage,
                    "evidence": evidence, "concerns": ["原参考不能成立"],
                    "reasoning": "脚本结论，只验证传输和聚合。"}
        raise AssertionError("Unexpected provider step: " + step)


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        # Match CLI main: Windows temporary roots may use an 8.3 alias.
        self.case = Path(self.temp).resolve()
        self.plan = deepcopy(PLAN)
        original_import = builtins.__import__
        def forbid_config(name, *args, **kwargs):
            if name.split(".")[0] in {"config", "dotenv"}:
                raise AssertionError("Offline test imported environment/provider configuration")
            return original_import(name, *args, **kwargs)
        self.stack.enter_context(patch("builtins.__import__", side_effect=forbid_config))
        self.stack.enter_context(patch("socket.socket", side_effect=AssertionError("Network disabled")))
        self.stack.enter_context(patch("socket.create_connection", side_effect=AssertionError("Network disabled")))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        for name, value in (("plan.json", self.plan), ("seed.json", {"private": "PRIVATE_SEED"}),
                            ("design_intent.json", {"intent": "PRIVATE_DESIGN_INTENT"}),
                            ("question_intent.json", {"intent": "PRIVATE_QUESTION_INTENT"}),
                            ("quality_review_protocol.json", {"scope": "offline transport test"})):
            driver.save(self.case / name, value)
        (self.case / "protocol.txt").write_text(PROTOCOL, encoding="utf-8")

    def material(self, provider=None):
        provider = provider or OfflineProvider()
        with patch.object(driver, "provider", side_effect=provider.factory):
            driver.run_materials(self.case, self.plan, True)
        return provider

    def questions(self, provider=None):
        provider = provider or OfflineProvider()
        if not (self.case / "material_freeze.json").exists():
            self.material(provider)
        with patch.object(driver, "provider", side_effect=provider.factory):
            driver.run_questions(self.case, self.plan, True)
        return provider

    def review(self, provider=None, workers=3):
        provider = provider or OfflineProvider()
        if not (self.case / "question_freeze.json").exists():
            self.questions(provider)
        with patch.object(driver, "provider", side_effect=provider.factory):
            driver.run_reviews(self.case, self.plan, True, workers)
        return driver.read(self.case / "reviews/report.json"), provider

    def replay_fixture(self, prefix=1):
        """Build a source-bound derived case from real offline batch reports."""
        self.material()
        derived = self.case / "derived"
        derived.mkdir()
        for name in ("seed.json", "design_intent.json", "question_intent.json",
                     "protocol.txt", "quality_review_protocol.json", "plan.json"):
            (derived / name).write_bytes((self.case / name).read_bytes())
        entries, sources = [], []
        for index in range(prefix):
            source = self.case / "materials" / f"batch_{index:02d}.json"
            batch = driver.read(source)
            call = next(e for e in batch["records"] if e["event"] == "started")
            entries.append({"step": call["step"], "messages": call["messages"],
                            "params": call["params"], "output": batch["raw_output"],
                            "provenance": {"source_file": str(source),
                                           "source_sha256": driver.sha(source),
                                           "kind": "offline_fixture_prior_parsed_output"}})
            sources.append({"path": str(source), "sha256": driver.sha(source)})
        manifest = derived / "material_replay.json"
        driver.save(manifest, {"source_case": str(self.case),
                               "source_files": sources, "entries": entries})
        return derived, manifest

    def test_all_dry_phases_never_load_provider_or_create_phase_outputs(self):
        with patch.object(driver, "provider", side_effect=AssertionError("dry provider")):
            driver.run_materials(self.case, self.plan, False)
        self.assertFalse((self.case / "materials").exists())
        self.material()
        with patch.object(driver, "provider", side_effect=AssertionError("dry provider")):
            driver.run_questions(self.case, self.plan, False)
        self.assertFalse((self.case / "questions").exists())
        self.questions()
        with patch.object(driver, "provider", side_effect=AssertionError("dry provider")):
            driver.run_reviews(self.case, self.plan, False, 3)
        self.assertFalse((self.case / "reviews").exists())

    def test_full_series_public_export_excludes_notes_and_preserves_protocol(self):
        provider = self.material()
        corpus = driver.read(self.case / "corpus.json")
        self.assertNotIn("PRIVATE_WRITER_NOTES", json.dumps(corpus))
        self.assertNotIn("PRIVATE_DOCUMENT_METADATA", json.dumps(corpus))
        self.assertEqual(driver.read(self.case / "about.json")["public_protocol"], PROTOCOL)
        self.assertEqual(len(provider.calls), 2)
        self.assertFalse(driver.read(self.case / "material_freeze.json")["semantic_approval"])

    def test_failed_material_batch_persists_failed_report_and_exports_no_whole_corpus(self):
        provider = OfflineProvider(material_failure=1)
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            self.material(provider)
        report = driver.read(self.case / "materials/report.json")
        self.assertEqual(len(report["partial_corpus"]["sessions"]), 1)
        self.assertEqual(len(report["batch_reports"]), 2)
        self.assertEqual(report["batch_reports"][1]["execution"]["status"], "model_error")
        self.assertFalse((self.case / "corpus.json").exists())
        self.assertFalse((self.case / "material_freeze.json").exists())

    def test_budget_exhausted_material_does_not_export_partial_as_complete(self):
        self.plan["budgets"]["material_calls"] = 1
        provider = OfflineProvider()
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            self.material(provider)
        self.assertEqual(len(provider.calls), 1)
        self.assertFalse((self.case / "material_freeze.json").exists())

    def test_completed_phase_refuses_overwrite_before_another_provider_call(self):
        provider = self.material()
        count = len(provider.calls)
        with self.assertRaises(FileExistsError):
            self.material(provider)
        self.assertEqual(len(provider.calls), count)

    def test_frozen_public_input_tamper_blocks_question_provider(self):
        self.material()
        (self.case / "about.json").write_text('{"public_protocol":"changed"}', encoding="utf-8")
        provider = OfflineProvider()
        with self.assertRaisesRegex(ValueError, "Frozen public input changed"):
            self.questions(provider)
        self.assertEqual(provider.calls, [])
        self.assertFalse((self.case / "questions").exists())

    def test_questions_keep_unreadable_raw_instances_but_review_only_readable_texts(self):
        provider = OfflineProvider(malformed_candidate=True)
        report, _ = self.review(provider)
        proposal = driver.read(self.case / "questions/report.json")
        self.assertEqual(len(proposal["candidates"]), 4)
        self.assertEqual(len(proposal["questions"]), 3)
        self.assertEqual(len(report["items"]), 3)
        self.assertEqual(proposal["quantity"]["unreadable_candidates"], 1)

    def test_parallel_partitions_bind_every_question_and_remap_trace_ids(self):
        report, provider = self.review()
        questions = driver.read(self.case / "questions.json")["questions"]
        expected = prepare_review(questions, driver.read(self.case / "corpus.json"), PROTOCOL,
                                  reader_model="offline-reader", reviewer_model="offline-reviewer")
        self.assertEqual(report["calls_used"], 8)
        self.assertEqual(len(report["items"]), 4)
        self.assertEqual(report["summary"], {"candidates": 4, "completed": 4, "pending": 0})
        self.assertEqual(report["publication_effect"], "none")
        for i, (item, bound) in enumerate(zip(report["items"], expected["items"])):
            self.assertEqual(item["binding"], bound["binding"])
            self.assertEqual(item["candidate_id"], bound["candidate_id"])
            self.assertEqual(item["source_qid"], questions[i]["qid"])
            self.assertEqual(item["reference_status"], "contradicted")
            self.assertEqual(item["blind_read"]["interpretation"], questions[i]["question"])
            events = [e for e in report["records"] if e["candidate_id"] == item["candidate_id"]]
            self.assertEqual(len(events), 4)
            for event in events:
                self.assertEqual(event["partition_candidate_id"], "q000001")
                self.assertEqual(event["binding"], item["binding"])
                self.assertEqual(event["partition_report"], f"item_{i+1:03d}/report.json")

    def test_blind_inputs_have_full_corpus_and_no_author_reference_or_legacy_labels(self):
        report, provider = self.review()
        reviews = [x for x in provider.calls if x["step"].startswith("semantic_review.")]
        for call in reviews:
            payload = json.loads(call["messages"][1]["content"])
            self.assertEqual(payload["documents"], report["documents"])
            self.assertEqual(payload["public_protocol"], PROTOCOL)
            for hidden in ("PRIVATE_WRITER_NOTES", "PRIVATE_SEED", "PRIVATE_TITLE",
                           "PRIVATE_DESIGN_INTENT", "PRIVATE_QUESTION_INTENT"):
                self.assertNotIn(hidden, call["messages"][1]["content"])
            if call["step"] == "semantic_review.blind_read":
                self.assertNotIn("reference_proposal", payload)
                self.assertNotIn("PRIVATE_REFERENCE", call["messages"][1]["content"])
            else:
                self.assertEqual(payload["reference_proposal"]["answer"], "PRIVATE_REFERENCE")

    def test_odd_review_budget_preserves_all_candidates_as_pending_when_unfinished(self):
        self.questions()
        self.plan["budgets"]["review_calls"] = 3
        report, provider = self.review()
        self.assertEqual(report["calls_used"], 3)
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(report["budget"]["per_candidate_calls"], [2, 1, 0, 0])
        self.assertEqual(report["summary"], {"candidates": 4, "completed": 1, "pending": 3})
        self.assertEqual(report["items"][1]["execution"]["status"], "call_budget_exhausted")
        self.assertIsNotNone(report["items"][1]["blind_read"])
        self.assertIsNone(report["items"][1]["adjudication"])

    def test_one_reader_error_does_not_drop_or_adjudicate_failed_item(self):
        self.questions()
        question = driver.read(self.case / "questions.json")["questions"][1]["question"]
        provider = OfflineProvider(review_failure=question)
        report, _ = self.review(provider)
        self.assertEqual(report["calls_used"], 7)
        failed = report["items"][1]
        self.assertEqual(failed["execution"]["status"], "model_error")
        self.assertEqual(failed["item_validity"], "unresolved")
        self.assertIsNone(failed["adjudication"])
        self.assertEqual(report["summary"], {"candidates": 4, "completed": 3, "pending": 1})

    def test_frozen_question_tamper_blocks_review_provider(self):
        self.questions()
        (self.case / "questions.json").write_text('{"questions":[]}', encoding="utf-8")
        provider = OfflineProvider()
        with self.assertRaisesRegex(ValueError, "Frozen questions changed"):
            self.review(provider)
        self.assertEqual(provider.calls, [])
        self.assertFalse((self.case / "reviews").exists())

    def test_replay_source_change_blocks_before_provider_loading_or_phase_claim(self):
        derived, manifest = self.replay_fixture()
        source = self.case / "materials/batch_00.json"
        source.write_bytes(source.read_bytes() + b"\n")
        with patch.object(driver, "provider", side_effect=AssertionError("provider loaded")):
            with self.assertRaisesRegex(ValueError, "Replay source changed"):
                driver.run_materials(derived, self.plan, True, manifest)
        self.assertFalse((derived / "materials").exists())

    def test_replay_dry_run_authenticates_sources_without_provider_or_output(self):
        derived, manifest = self.replay_fixture()
        with patch.object(driver, "provider", side_effect=AssertionError("dry provider")):
            driver.run_materials(derived, self.plan, False, manifest)
        self.assertFalse((derived / "materials").exists())

    def test_replayed_prefix_uses_zero_live_calls_and_suffix_counts_separately(self):
        derived, manifest = self.replay_fixture(prefix=1)
        provider = OfflineProvider()
        with patch.object(driver, "provider", side_effect=provider.factory):
            driver.run_materials(derived, self.plan, True, manifest)
        report = driver.read(derived / "materials/report.json")
        summary = report["execution_lineage"]["summary"]
        self.assertEqual(report["calls_used"], 2)
        self.assertEqual(summary["replayed_calls"], 1)
        self.assertEqual(summary["real_calls"], 1)
        self.assertEqual(summary["remaining"], 0)
        self.assertFalse(summary["blocked"])
        self.assertEqual(len(provider.calls), 1)
        live_payload = json.loads(provider.calls[0]["messages"][1]["content"])
        self.assertEqual(live_payload["design_intent"]["scheduled_batch"]["session_id"], 1)
        self.assertEqual(report["batch_reports"][0]["raw_output"],
                         driver.read(self.case / "materials/batch_00.json")["raw_output"])
        replay_events = [e for e in provider.events if e["event"] == "replay"]
        self.assertEqual(len(replay_events), 1)
        self.assertFalse(replay_events[0]["real_provider_call"])

    def test_full_replay_makes_no_real_provider_attempt(self):
        derived, manifest = self.replay_fixture(prefix=2)
        provider = OfflineProvider()
        with patch.object(driver, "provider", side_effect=provider.factory):
            driver.run_materials(derived, self.plan, True, manifest)
        report = driver.read(derived / "materials/report.json")
        self.assertEqual(report["calls_used"], 2)
        self.assertEqual(report["execution_lineage"]["summary"]["replayed_calls"], 2)
        self.assertEqual(report["execution_lineage"]["summary"]["real_calls"], 0)
        self.assertEqual(provider.calls, [])

    def test_replay_identity_mismatch_stops_without_live_fallback(self):
        derived, manifest = self.replay_fixture(prefix=1)
        data = driver.read(manifest)
        data["entries"][0]["params"]["max_tokens"] += 1
        manifest.write_text(json.dumps(data), encoding="utf-8")
        provider = OfflineProvider()
        with patch.object(driver, "provider", side_effect=provider.factory):
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                driver.run_materials(derived, self.plan, True, manifest)
        report = driver.read(derived / "materials/report.json")
        summary = report["execution_lineage"]["summary"]
        self.assertEqual(summary["mismatch_count"], 1)
        self.assertTrue(summary["blocked"])
        self.assertEqual(summary["real_calls"], 0)
        self.assertEqual(summary["replayed_calls"], 0)
        self.assertEqual(provider.calls, [])
        self.assertEqual(report["batch_reports"][0]["execution"]["error_type"], "ReplayMismatchError")
        self.assertFalse((derived / "material_freeze.json").exists())

    def test_logical_material_budget_includes_replayed_prefix_and_blocks_suffix(self):
        derived, manifest = self.replay_fixture(prefix=1)
        plan = deepcopy(self.plan)
        plan["budgets"]["material_calls"] = 1
        provider = OfflineProvider()
        with patch.object(driver, "provider", side_effect=provider.factory):
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                driver.run_materials(derived, plan, True, manifest)
        report = driver.read(derived / "materials/report.json")
        summary = report["execution_lineage"]["summary"]
        self.assertEqual(report["calls_used"], 1)
        self.assertEqual(summary["replayed_calls"], 1)
        self.assertEqual(summary["real_calls"], 0)
        self.assertEqual(provider.calls, [])
        self.assertEqual(report["batch_reports"][1]["execution"]["status"], "call_budget_exhausted")
        self.assertFalse((derived / "material_freeze.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
