"""Offline CLI persistence, freeze, budget and quality-authority integration."""
from __future__ import annotations

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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_agent_case as cli
from pipeline.agent_factory import AgentCaseError, PHASES
from pipeline.quality_workflow import validate_review
from agent_factory_selftest import Script, TeamScript, team_plan, plan as factory_plan, PROTOCOL


def sha(data):
    return hashlib.sha256(data).hexdigest()


class AgentCaseCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.case = Path(self.temp.name)
        for name, value in (("seed.json", {"private": "PRIVATE_SEED"}),
                            ("design_intent.json", {"intent": "PRIVATE_DESIGN"}),
                            ("question_intent.json", {"intent": "PRIVATE_QUESTION_DESIGN"})):
            (self.case / name).write_text(json.dumps(value), encoding="utf-8")
        (self.case / "protocol.txt").write_text(PROTOCOL, encoding="utf-8")
        self.plan = {"version": cli.PLAN_VERSION, "result_scope": "research_only", "factory_plan": factory_plan(),
            "input_sha256": {name: sha((self.case / name).read_bytes()) for name in cli.INPUTS if name != "plan.json"},
            "source_sha256": {name: sha((ROOT / name).read_bytes()) for name in cli.SOURCES}}
        self.write_plan()

    def write_plan(self):
        (self.case / "plan.json").write_text(json.dumps(self.plan, ensure_ascii=False), encoding="utf-8")

    def inject(self, script, record=None):
        self.events = []
        return patch.object(cli, "provider", return_value=(script, record or self.events.append, deepcopy))

    def configure_team(self):
        self.plan.update(version=cli.TEAM_PLAN_VERSION, factory_plan=team_plan(),
            source_sha256={name: sha((ROOT / name).read_bytes()) for name in cli.TEAM_SOURCES})
        self.write_plan()

    def test_dry_run_has_no_config_provider_calls_or_output_directory(self):
        before = {p.name: p.read_bytes() for p in self.case.iterdir()}
        with patch.object(cli, "provider", side_effect=AssertionError("provider init")):
            result = cli.run(self.case)
        self.assertEqual(result["calls_permitted"], 0)
        self.assertEqual(result["planned_max_provider_attempts"], 14)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.case.iterdir()})
        code = """import builtins, json, sys
original_import = builtins.__import__
def checked(name, *a, **kw):
    if name.split('.')[0] in {'config', 'openai', 'dotenv'}:
        raise AssertionError('No provider imports in dry run: ' + name)
    return original_import(name, *a, **kw)
builtins.__import__ = checked
from tools.run_agent_case import run
print(json.dumps(run(sys.argv[1])))
assert 'config' not in sys.modules
"""
        completed = subprocess.run([sys.executable, "-c", code, str(self.case)], cwd=ROOT,
                                   capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["calls_permitted"], 0)

    def test_input_or_source_change_rejected_before_provider_or_claim(self):
        for target in ("input", "source", "missing_source"):
            before = deepcopy(self.plan)
            if target == "input":
                self.plan["input_sha256"]["seed.json"] = "0" * 64
            elif target == "source":
                self.plan["source_sha256"][cli.SOURCES[0]] = "0" * 64
            else:
                self.plan["source_sha256"].pop(cli.SOURCES[-1])
            self.write_plan()
            with self.subTest(target=target), patch.object(cli, "provider") as provider, self.assertRaises(ValueError):
                cli.run(self.case, execute=True)
            provider.assert_not_called()
            self.assertFalse((self.case / "execution").exists())
            self.plan = before
        self.write_plan()

    def test_real_composition_checkpoints_each_phase_before_next_call_and_exports_separate_claims(self):
        script = Script()
        observed = []
        def call(step, messages, **params):
            phase = params["model"].split(".")[0]
            if observed and phase != observed[-1]:
                self.assertTrue((self.case / "execution/phases" / observed[-1] / "report.json").exists())
            observed.append(phase)
            return script(step, messages, **params)
        with self.inject(call):
            result = cli.run(self.case, execute=True)
        directory = self.case / "execution"
        self.assertEqual(result["actual_provider_attempts"], 14)
        self.assertEqual(len(script.calls), 14)
        self.assertEqual(set(p.name for p in (directory / "phases").iterdir()), set(PHASES))
        report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
        current = json.loads((directory / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(current, report["current_snapshot"])
        validate_review(current, report["phases"]["final_review"])
        receipt = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
        status = json.loads((directory / "quality_status.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["counts"]["model_review_eligible"], 2)
        self.assertFalse(receipt["material_ready"])
        self.assertFalse(receipt["scoring_ready"])
        self.assertFalse(status["official_release"])
        self.assertFalse(status["material_ready"])
        self.assertEqual(status["execution"]["status"], "completed")
        self.assertEqual(status["material_acceptance"]["authority"], "none")
        for name in cli.INPUTS:
            self.assertEqual((directory / "inputs" / name).read_bytes(), (self.case / name).read_bytes())
        for name in cli.SOURCES:
            self.assertEqual(sha((directory / "source_snapshot" / name).read_bytes()), self.plan["source_sha256"][name])
        self.assertTrue(all(event["phase"] in PHASES for event in self.events))
        original = (directory / "report.json").read_bytes()
        with patch.object(cli, "provider") as provider, self.assertRaises(FileExistsError):
            cli.run(self.case, execute=True)
        provider.assert_not_called()
        self.assertEqual((directory / "report.json").read_bytes(), original)

    def test_material_transport_failure_is_preserved_without_candidate_snapshot(self):
        script = Script(bad_generation=True)
        with self.inject(script):
            result = cli.run(self.case, execute=True)
        directory = self.case / "execution"
        self.assertEqual(len(script.calls), 1)
        self.assertEqual(result["execution"]["status"], "stopped")
        self.assertFalse((directory / "snapshot.json").exists())
        report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["budget"]["actual_provider_attempts"], 1)
        self.assertEqual(report["receipt"]["original_count"], 0)
        self.assertTrue((directory / "phases/material_generation/report.json").exists())
        self.assertEqual(set(p.name for p in (directory / "phases").iterdir()), set(PHASES))

    def test_zero_global_budget_never_calls_provider_and_stays_unaccepted(self):
        self.plan["factory_plan"]["max_provider_attempts"] = 0
        self.write_plan()
        script = Script()
        with self.inject(script):
            result = cli.run(self.case, execute=True)
        self.assertEqual(script.calls, [])
        self.assertEqual(result["actual_provider_attempts"], 0)
        self.assertFalse((self.case / "execution/snapshot.json").exists())

    def test_phase_checkpoint_failure_halts_calls_and_saves_attached_partial_case(self):
        script, original_save = Script(), cli.save
        def save(path, value):
            if Path(path).name == "case_checkpoint.json":
                raise OSError("injected checkpoint storage failure")
            original_save(path, value)
        with self.inject(script), patch.object(cli, "save", side_effect=save), self.assertRaises(AgentCaseError):
            cli.run(self.case, execute=True)
        directory = self.case / "execution"
        self.assertEqual(len(script.calls), 1)
        self.assertTrue((directory / "phases/material_generation/report.json").exists())
        partial = json.loads((directory / "failed_report.json").read_text(encoding="utf-8"))
        self.assertEqual(partial["execution"]["status"], "audit_error")
        self.assertEqual(partial["budget"]["actual_provider_attempts"], 1)
        self.assertTrue((directory / "failure.json").exists())
        self.assertFalse((directory / "snapshot.json").exists())
        with patch.object(cli, "provider") as provider, self.assertRaises(FileExistsError):
            cli.run(self.case, execute=True)
        provider.assert_not_called()

    def test_provider_initialization_failure_leaves_frozen_inputs_and_claimed_failure(self):
        with patch.object(cli, "provider", side_effect=RuntimeError("not initialized")), self.assertRaises(RuntimeError):
            cli.run(self.case, execute=True)
        directory = self.case / "execution"
        self.assertTrue((directory / "manifest.json").exists())
        self.assertTrue((directory / "inputs/seed.json").exists())
        self.assertTrue((directory / "failure.json").exists())
        self.assertFalse((directory / "snapshot.json").exists())

    def test_team_mode_freezes_dependencies_and_writes_nested_complete_checkpoints(self):
        self.configure_team()
        script = TeamScript()
        with self.inject(script):
            result = cli.run(self.case, execute=True)
        self.assertEqual(result["actual_provider_attempts"], 19)
        directory = self.case / "execution"
        for name in cli.TEAM_SOURCES:
            self.assertEqual(sha((directory / "source_snapshot" / name).read_bytes()), self.plan["source_sha256"][name])
        for role in ("continuity_reader", "use_reader", "triage", "edit", "fresh_reader", "decision"):
            folder = directory / "phases/material_team" / role
            self.assertTrue((folder / "report.json").exists())
            self.assertTrue((folder / "case_checkpoint.json").exists())
            self.assertTrue((folder / "material_team_checkpoint.json").exists())
        team = json.loads((directory / "phases/material_team/report.json").read_text(encoding="utf-8"))
        final_checkpoint = json.loads((directory / "phases/material_team/decision/material_team_checkpoint.json").read_text(encoding="utf-8"))
        self.assertEqual(final_checkpoint, team)
        self.assertTrue(team["accepted_for_question_generation"])
        status = json.loads((directory / "quality_status.json").read_text(encoding="utf-8"))
        self.assertTrue(status["material_ready"])
        self.assertEqual(status["material_acceptance"]["authority"], "model")
        self.assertFalse(status["scoring_ready"])
        self.assertFalse(status["official_release"])
        self.assertTrue((directory / "material_snapshot.json").exists())
        self.assertTrue((directory / "original_material_snapshot.json").exists())
        self.assertTrue((directory / "phases/collection_review/report.json").exists())
        self.assertTrue((directory / "phases/final_collection_review/report.json").exists())
        collection = json.loads((directory / "collection_assessment.json").read_text(encoding="utf-8"))
        receipt = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(collection, receipt["collection_assessment"])
        self.assertFalse(collection["improvement_verified"])
        self.assertEqual(collection["final"]["report"]["execution"]["status"], "ok")

    def test_team_hold_exports_material_and_diagnostics_without_questions(self):
        self.configure_team()
        with self.inject(TeamScript(team_action="hold")):
            result = cli.run(self.case, execute=True)
        directory = self.case / "execution"
        self.assertEqual(result["actual_provider_attempts"], 4)
        self.assertEqual(result["execution"]["reason"], "material_team_held")
        self.assertTrue((directory / "material_snapshot.json").exists())
        self.assertTrue((directory / "phases/material_team/triage/report.json").exists())
        self.assertFalse((directory / "snapshot.json").exists())
        receipt = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
        self.assertFalse(receipt["material_ready"])
        self.assertEqual(receipt["original_count"], 0)

    def test_team_mode_needs_matching_plan_version_and_complete_source_set(self):
        self.configure_team()
        frozen = deepcopy(self.plan)
        for mutation in (lambda p: p.update(version=cli.PLAN_VERSION),
                         lambda p: p["source_sha256"].pop("pipeline/material_team.py"),
                         lambda p: p["source_sha256"].pop("pipeline/question_set_review.py")):
            self.plan = deepcopy(frozen); mutation(self.plan); self.write_plan()
            with patch.object(cli, "provider") as provider, self.assertRaises(ValueError):
                cli.run(self.case, execute=True)
            provider.assert_not_called()
            self.assertFalse((self.case / "execution").exists())

    def test_nested_team_checkpoint_failure_keeps_parent_history_and_attempts(self):
        self.configure_team()
        script, original_save = TeamScript(), cli.save
        def failing_save(path, value):
            if Path(path).parent.name == "use_reader" and Path(path).name == "case_checkpoint.json":
                raise OSError("nested disk failure")
            original_save(path, value)
        with self.inject(script), patch.object(cli, "save", side_effect=failing_save), self.assertRaises(AgentCaseError):
            cli.run(self.case, execute=True)
        directory = self.case / "execution"
        partial = json.loads((directory / "failed_report.json").read_text(encoding="utf-8"))
        self.assertEqual(partial["budget"]["actual_provider_attempts"], 3)
        self.assertEqual(len(script.calls), 3)
        self.assertEqual(partial["execution"]["status"], "audit_error")
        self.assertIn("continuity_reader", partial["phases"]["material_team"]["partial_report"]["phases"])
        self.assertTrue((directory / "phases/material_team/continuity_reader/report.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
