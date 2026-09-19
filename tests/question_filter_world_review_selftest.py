"""Provider-free regression for carrying the original world gate through filtering.

Fake reviewer acceptance proves plumbing and receipt identity, not world semantics.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import question_filter_selftest as fixtures
import config
from eval import question_filter
from pipeline import world_semantics
from pipeline.quality import evaluate_release, require_release
from pipeline.world_state import WorldState

config.REVIEWER_MODEL = "offline-world-export-fixture"
BOUND_FILES = ("01_whitepaper.json", "02_world.json", "00_input.json", world_semantics.REVIEW_ARTIFACT)
ACCEPT = {"decision": "accept", "reason": "Only an offline wiring fixture.", "issues": [],
          "mechanism_coverage": [], "repair_targets": {"intrinsic": [], "structure": False},
          "limitations": "Does not establish semantic correctness."}


def read(directory, name):
    return json.loads((directory / name).read_text(encoding="utf-8"))


def write(directory, name, value):
    (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class WorldReviewExportTest(unittest.TestCase):
    def setUp(self):
        self.network_guard = patch("socket.socket", side_effect=AssertionError("No network in offline test"))
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)

    def source(self, directory, *, enabled_by="whitepaper"):
        bench, results, _ = fixtures.DerivedReleaseTest().make_source(directory)
        if enabled_by is None:
            return bench, results
        wp, manifest = read(directory, "01_whitepaper.json"), read(directory, "manifest.json")
        if enabled_by == "whitepaper":
            wp["quality_contract"] = {"world_semantic_review": True}
        elif enabled_by == "config":
            manifest["config"] = {"world_semantic_review": True}
        else:
            raise AssertionError(enabled_by)
        task = {"scenario": "office", "description": "Offline status record fixture."}
        ws = WorldState.from_dict(read(directory, "02_world.json"))
        tracer = SimpleNamespace(chat_json=Mock(return_value=copy.deepcopy(ACCEPT)))
        review = world_semantics.review_world(wp, ws, tracer, task_input=task)
        self.assertEqual(review["status"], "passed", review)
        self.assertEqual(world_semantics.validate_review(review, wp, ws, task_input=task), [])
        tracer.chat_json.assert_called_once()
        write(directory, "01_whitepaper.json", wp)
        write(directory, "manifest.json", manifest)
        write(directory, "00_input.json", task)
        write(directory, world_semantics.REVIEW_ARTIFACT, review)
        receipt = evaluate_release(directory)
        self.assertTrue(receipt["eligible"], receipt["issues"])
        write(directory, "07_release.json", receipt)
        self.assertTrue(require_release(bench)["eligible"])
        return bench, results

    def break_source(self, source, mode):
        if mode == "missing_review":
            (source / world_semantics.REVIEW_ARTIFACT).unlink()
        elif mode == "missing_task":
            (source / "00_input.json").unlink()
        elif mode == "stale_task":
            task = read(source, "00_input.json")
            task["description"] = "Changed task after world review."
            write(source, "00_input.json", task)
        elif mode == "negative_review":
            raw = {**ACCEPT, "decision": "unresolved", "reason": "Fixture review cannot establish sufficiency."}
            report = world_semantics.review_world(read(source, "01_whitepaper.json"),
                WorldState.from_dict(read(source, "02_world.json")),
                SimpleNamespace(chat_json=Mock(return_value=raw)), task_input=read(source, "00_input.json"))
            self.assertEqual(report["status"], "unresolved")
            write(source, world_semantics.REVIEW_ARTIFACT, report)
        elif mode == "malformed_review":
            (source / world_semantics.REVIEW_ARTIFACT).write_text("{broken", encoding="utf-8")
        else:
            raise AssertionError(mode)

    def test_effective_opt_in_and_exact_inputs_survive_eligible_export(self):
        for enabled_by in ("whitepaper", "config"):
            with self.subTest(enabled_by=enabled_by), tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp)
                bench, results = self.source(source, enabled_by=enabled_by)
                out = source / "filtered"
                report = question_filter.export_filtered_benchmark(bench, results, out)
                self.assertEqual(report["result_scope"], "release_eligible", report["release"])
                self.assertTrue(require_release(out / bench.name)["eligible"])
                manifest = read(out, "manifest.json")
                self.assertIs(manifest["config"]["world_semantic_review"], True)
                validation = manifest["derived_from"]["world_semantic_review"]
                self.assertEqual(validation["validation"], {"status": "passed", "issues": []})
                for name in BOUND_FILES:
                    raw = (source / name).read_bytes()
                    self.assertEqual((out / name).read_bytes(), raw)
                    self.assertEqual(validation["input_hashes"][name], hashlib.sha256(raw).hexdigest())
                self.assertEqual(world_semantics.validate_review(read(out, world_semantics.REVIEW_ARTIFACT),
                    read(out, "01_whitepaper.json"), WorldState.from_dict(read(out, "02_world.json")),
                    task_input=read(out, "00_input.json")), [])

    def test_invalid_original_review_refuses_before_creating_target(self):
        for mode in ("missing_review", "missing_task", "stale_task", "negative_review", "malformed_review"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp)
                bench, results = self.source(source, enabled_by="config")
                self.break_source(source, mode)
                out = source / "filtered"
                # Exercise export's own check even if a caller supplies a cached
                # source-release response that predates the changed world inputs.
                with patch("pipeline.quality.require_release", return_value={"eligible": True, "status": "passed"}):
                    with self.assertRaisesRegex(ValueError, "源世界业务审阅"):
                        question_filter.export_filtered_benchmark(bench, results, out)
                self.assertFalse(out.exists())

    def test_explicit_research_export_keeps_failure_and_requirement(self):
        for mode in ("missing_review", "missing_task", "stale_task", "negative_review", "malformed_review"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp)
                bench, results = self.source(source, enabled_by="config")
                self.break_source(source, mode)
                out = source / "research"
                report = question_filter.export_filtered_benchmark(bench, results, out, allow_unverified=True)
                self.assertEqual(report["result_scope"], "research_only")
                self.assertFalse(report["release"]["eligible"])
                self.assertFalse(evaluate_release(out)["eligible"])
                manifest = read(out, "manifest.json")
                self.assertIs(manifest["config"]["world_semantic_review"], True)
                self.assertIs(manifest["release_policy"]["inherited_research_only"], True)
                validation = manifest["derived_from"]["world_semantic_review"]["validation"]
                self.assertEqual(validation["status"], "failed")
                self.assertTrue(validation["issues"])
                for name in BOUND_FILES:
                    if (source / name).exists():
                        self.assertEqual((out / name).read_bytes(), (source / name).read_bytes())
                    else:
                        self.assertFalse((out / name).exists())

    def test_copies_verified_snapshot_if_source_changes_after_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            bench, results = self.source(source)
            frozen = {name: (source / name).read_bytes() for name in BOUND_FILES}
            original_filter = question_filter.filter_questions

            def mutate_then_filter(*args, **kwargs):
                self.break_source(source, "stale_task")
                return original_filter(*args, **kwargs)

            out = source / "filtered"
            with patch.object(question_filter, "filter_questions", side_effect=mutate_then_filter):
                report = question_filter.export_filtered_benchmark(bench, results, out)
            self.assertTrue(report["release"]["eligible"], report["release"])
            self.assertNotEqual((source / "00_input.json").read_bytes(), frozen["00_input.json"])
            for name, raw in frozen.items():
                self.assertEqual((out / name).read_bytes(), raw)
                self.assertIn({"path": str((source / name).resolve()), "sha256": hashlib.sha256(raw).hexdigest()},
                              report["input_files"])

    def test_legacy_without_opt_in_has_no_new_obligation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            bench, results = self.source(source, enabled_by=None)
            out = source / "filtered"
            with patch.object(world_semantics, "validate_review", side_effect=AssertionError("Legacy has no review obligation")):
                report = question_filter.export_filtered_benchmark(bench, results, out)
            self.assertTrue(report["release"]["eligible"])
            manifest = read(out, "manifest.json")
            self.assertNotIn("world_semantic_review", manifest.get("config", {}))
            self.assertNotIn("world_semantic_review", manifest["derived_from"])
            self.assertFalse((out / "00_input.json").exists())
            self.assertFalse((out / world_semantics.REVIEW_ARTIFACT).exists())


if __name__ == "__main__":
    unittest.main()
