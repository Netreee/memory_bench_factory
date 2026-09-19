"""Offline filesystem and injected-provider regressions for the B/C driver."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_bc_workflow as driver
from pipeline.quality_workflow import snapshot, validate_review
from quality_workflow_selftest import CORPUS, PROTOCOL, QUESTIONS, ReaderScript


def issue_responses(payload):
    return [{"issue_id": issue["issue_id"], "disposition": "disputed",
             "reason": "离线脚本保留原稿；不声称语义验收已完成。"} for issue in payload["issues"]]


class EditingScript:
    def __init__(self, fail_on=None, unresolved=False):
        self.calls = []
        self.fail_on = fail_on
        self.unresolved = unresolved

    def __call__(self, step, messages, **params):
        payload = json.loads(messages[1]["content"])
        self.calls.append({"step": step, "payload": payload, "params": deepcopy(params)})
        if self.fail_on == len(self.calls):
            raise RuntimeError("injected provider failure")
        if "questions" in payload:
            return {"decisions": [{"qid": q["qid"], "action": "keep", "reason": "离线脚本保留原题。"}
                                  for q in payload["questions"]], "issue_responses": issue_responses(payload)}
        return {"action": "unresolved" if self.unresolved else "no_change", "reason": "离线脚本暂不修改。",
                "document_edits": [], "issue_responses": issue_responses(payload)}


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.case = Path(self.temp.name)
        self.original = snapshot(QUESTIONS, CORPUS, PROTOCOL)
        self.plan = {"result_scope": "research_only", "source_sha256": {
            source: driver.sha(ROOT / source) for source in driver.SOURCES},
            "budgets": {"material_edit": 1, "material_review": 1, "question_edit": 2,
                        "final_review": 4, "receipt": 0},
            "max_input_chars": 200000, "editor_model": "offline-editor", "reviewer_model": "offline-critic",
            "reader_model": "offline-reader", "material_edit_max_tokens": 4096,
            "question_edit_max_tokens": 4096, "review_max_tokens": 4096, "question_batch_size": 1}
        driver.save(self.case / "plan.json", self.plan)
        driver.save(self.case / "original_snapshot.json", self.original)
        driver.save(self.case / "development_issues.json", {
            "material_issues": [{"issue_id": "m1", "description": "PRIVATE_MATERIAL_CRITIC"}],
            "question_issues": [{"issue_id": "q1", "qid": "q-client", "description": "PRIVATE_QUESTION_CRITIC"}]})

    def set_plan(self, plan):
        (self.case / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    def inject(self, call):
        self.events = []
        return patch.object(driver, "provider", return_value=(call, self.events.append, deepcopy))

    def material_snapshot(self):
        driver.save(self.case / "material_snapshot.json", self.original)

    def test_dry_run_never_loads_config_or_provider_and_creates_no_phase(self):
        before = {p.name for p in self.case.iterdir()}
        with patch.object(driver, "provider", side_effect=AssertionError("provider loaded")):
            result = driver.run(self.case, "material_edit")
        self.assertEqual(result["calls_permitted"], 0)
        self.assertEqual({p.name for p in self.case.iterdir()}, before)
        code = """import builtins, json, sys
original_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'config' or name.startswith('openai'):
        raise AssertionError('provider/config import during dry run: ' + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded
from tools.run_bc_workflow import run
print(json.dumps(run(sys.argv[1], 'material_edit')))
assert 'config' not in sys.modules
"""
        completed = subprocess.run([sys.executable, "-c", code, str(self.case)], cwd=ROOT,
                                   capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["calls_permitted"], 0)

    def test_source_freeze_mismatch_rejects_before_claiming_phase_or_provider(self):
        plan = deepcopy(self.plan)
        plan["source_sha256"][driver.SOURCES[0]] = "0" * 64
        self.set_plan(plan)
        with patch.object(driver, "provider") as provider, self.assertRaisesRegex(ValueError, "Frozen implementation changed"):
            driver.run(self.case, "material_edit", execute=True)
        provider.assert_not_called()
        self.assertFalse((self.case / "material_edit").exists())

    def test_snapshot_edit_without_new_binding_is_rejected(self):
        original = deepcopy(self.original)
        original["corpus"]["sessions"][0]["docs"][1]["content"] += "未重新冻结的改写。"
        (self.case / "original_snapshot.json").write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
        with patch.object(driver, "provider") as provider, self.assertRaisesRegex(ValueError, "Snapshot content/binding mismatch"):
            driver.run(self.case, "material_edit", execute=True)
        provider.assert_not_called()

    def test_successful_phase_cannot_be_overwritten_or_silently_rerun(self):
        call = EditingScript()
        with self.inject(call):
            result = driver.run(self.case, "material_edit", execute=True)
        self.assertEqual(result["provider_attempts"], 1)
        self.assertEqual(len(call.calls), 1)
        snapshot_path = self.case / "material_snapshot.json"
        before = snapshot_path.read_bytes()
        report_before = (self.case / "material_edit/report.json").read_bytes()
        with patch.object(driver, "provider") as provider, self.assertRaises(FileExistsError):
            driver.run(self.case, "material_edit", execute=True)
        provider.assert_not_called()
        self.assertEqual(snapshot_path.read_bytes(), before)
        self.assertEqual((self.case / "material_edit/report.json").read_bytes(), report_before)
        self.assertEqual(driver.checked_snapshot(snapshot_path)["parent_snapshot_id"], self.original["snapshot_id"])

    def test_failed_material_call_preserves_audit_without_exporting_snapshot_or_retry(self):
        call = EditingScript(fail_on=1)
        with self.inject(call), self.assertRaisesRegex(RuntimeError, "Material revision not ready"):
            driver.run(self.case, "material_edit", execute=True)
        self.assertFalse((self.case / "material_snapshot.json").exists())
        self.assertEqual(len(call.calls), 1)
        report = driver.read(self.case / "material_edit/report.json")
        self.assertFalse(report["proposal_ready"])
        self.assertEqual(driver.read(self.case / "material_edit/attempt_count.json")["actual_provider_attempts"], 1)
        self.assertTrue(self.events)
        with patch.object(driver, "provider") as provider, self.assertRaises(FileExistsError):
            driver.run(self.case, "material_edit", execute=True)
        provider.assert_not_called()

    def test_unresolved_material_proposal_does_not_export_snapshot(self):
        call = EditingScript(unresolved=True)
        with self.inject(call), self.assertRaisesRegex(RuntimeError, "Material revision not ready"):
            driver.run(self.case, "material_edit", execute=True)
        report = driver.read(self.case / "material_edit/report.json")
        self.assertTrue(report["proposal_ready"])
        self.assertEqual(report["proposal_state"], "unresolved")
        self.assertFalse((self.case / "material_snapshot.json").exists())

    def test_failed_later_question_batch_exports_no_partial_current_snapshot(self):
        self.material_snapshot()
        call = EditingScript(fail_on=2)
        with self.inject(call), self.assertRaisesRegex(RuntimeError, "no partial current snapshot"):
            driver.run(self.case, "question_edit", execute=True)
        self.assertEqual(len(call.calls), 2)
        self.assertFalse((self.case / "current_snapshot.json").exists())
        self.assertFalse((self.case / "change_impact.json").exists())
        self.assertTrue((self.case / "question_edit/batch_01.json").exists())
        self.assertTrue((self.case / "question_edit/batch_02.json").exists())
        report = driver.read(self.case / "question_edit/report.json")
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["calls_used"], 2)
        self.assertTrue(report["raw_preserved"])

    def test_zero_budget_material_phase_makes_no_provider_attempt(self):
        plan = deepcopy(self.plan)
        plan["budgets"]["material_edit"] = 0
        self.set_plan(plan)
        call = Mock(side_effect=AssertionError("zero budget called provider"))
        with self.inject(call), self.assertRaisesRegex(RuntimeError, "Material revision not ready"):
            driver.run(self.case, "material_edit", execute=True)
        call.assert_not_called()
        self.assertEqual(driver.read(self.case / "material_edit/attempt_count.json"), {
            "actual_provider_attempts": 0, "max_calls": 0})
        self.assertFalse((self.case / "material_snapshot.json").exists())

    def test_final_review_uses_current_snapshot_without_development_issue_leak(self):
        self.material_snapshot()
        revised_questions = deepcopy(QUESTIONS)
        revised_questions[0]["reference_proposal"]["rationale"] = "修改后的原参考理由。"
        current = snapshot(revised_questions, CORPUS, PROTOCOL, parent=self.original)
        driver.save(self.case / "current_snapshot.json", current)
        call = ReaderScript()
        with self.inject(call):
            result = driver.run(self.case, "final_review", execute=True, workers=2)
        self.assertEqual(result["provider_attempts"], 4)
        report = driver.read(self.case / "final_review/report.json")
        self.assertEqual(report["snapshot_id"], current["snapshot_id"])
        self.assertNotEqual(report["snapshot_id"], self.original["snapshot_id"])
        self.assertEqual(set(validate_review(current, report)), {"q-client", "q-market"})
        with self.assertRaises(ValueError):
            validate_review(self.original, report)
        for event in call.calls:
            payload = event["payload"]
            encoded = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("PRIVATE", encoded)
            self.assertNotIn("issues", payload)
            own = next(q for q in revised_questions if q["question"] == payload["question"])
            other = next(q for q in revised_questions if q["qid"] != own["qid"])
            self.assertNotIn(other["question"], encoded)
            if event["stage"] == "adjudicate":
                self.assertEqual(payload["reference_proposal"], own["reference_proposal"])
        self.assertEqual(len(list((self.case / "final_review").glob("item_*.json"))), 2)
        self.assertEqual(driver.read(self.case / "final_review/attempt_count.json")["actual_provider_attempts"], 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
