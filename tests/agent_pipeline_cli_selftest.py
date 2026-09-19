"""Offline integration for the thin user entry; no quality or API claims."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import agent_pipeline as cli
from tools import run_agent_case as generation
from tools import run_agent_evaluation as evaluation
from pipeline.agent_factory import run_agent_case
from agent_factory_selftest import team_plan, TeamScript
from agent_evaluation_selftest import transport


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = self.base / "spec folder"
        self.config.mkdir()
        self.net = patch("socket.socket", side_effect=AssertionError("Network forbidden"))
        self.net.start()
        self.addCleanup(self.net.stop)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    def gen_spec(self):
        for name, value in (("seed.json", {"goal": "Synthetic business materials"}),
                            ("design.json", {"purpose": "Independent reader exercise"}),
                            ("questions.json", {"goal": "Read the actual public material"})):
            self.write(self.config / name, value)
        # Preserve BOM/newlines exactly; relative paths must not use cwd.
        (self.config / "protocol.txt").write_bytes(b"\xef\xbb\xbfPublic protocol.\r\n")
        spec = {"version": cli.GENERATION_SPEC, "seed": "seed.json", "design_intent": "design.json",
            "question_intent": "questions.json", "public_protocol": "protocol.txt",
            "factory_plan": team_plan(), "transport": None}
        return self.write(self.config / "generation.json", spec)

    def completed_source(self, *, drop=True):
        source = self.base / "completed source"
        directory = source / "execution"
        directory.mkdir(parents=True)
        script = TeamScript(drop_second=drop)
        report = run_agent_case({"goal": "fixture"}, {"goal": "fixture"}, {"goal": "fixture"},
            "Only public materials.", plan=team_plan(), chat_json=script)
        generation._exports(directory, report, deepcopy)
        self.write(directory / "phases/final_review/report.json", report["phases"]["final_review"])
        return source, report

    def eval_spec(self, source):
        inputs = self.config / "calibration"
        results = {"outcomes": [{"control": "fixture", "records": [
            {"name": "prediction", "judgement": {"verdict": "correct"}}]}]}
        for name, value in (("results", results), ("plan", {}), ("semantic_audit", {})):
            self.write(inputs / (name + ".json"), value)
        spec = {"version": cli.EVALUATION_SPEC,
            "source_case": os.path.relpath(source, self.config),
            "calibration": {name: "calibration/" + name + ".json" for name in ("results", "plan", "semantic_audit")},
            "evaluation_plan": {"systems": [{"name": "A", "model": "solver-a", "max_tokens": 128},
                {"name": "B", "model": "solver-b", "max_tokens": 256}],
                "judge": {"model": "grader", "max_tokens": 384}, "transport": transport(),
                "max_provider_attempts": 4, "keep_easy_ratio": 0.5, "filter_seed": 17,
                "full_context_char_budget": 200000, "max_input_chars": 200000}}
        return self.write(self.config / "evaluation.json", spec)

    def test_generation_real_prepare_preserves_bytes_relative_paths_and_no_execution(self):
        spec, case = self.gen_spec(), self.base / "new case"
        with patch.object(generation, "provider", side_effect=AssertionError("No provider in prepare")):
            result = cli.prepare_generation(spec, case)
        self.assertTrue(result["ready"])
        self.assertEqual((case / "protocol.txt").read_bytes(), (self.config / "protocol.txt").read_bytes())
        self.assertEqual((case / "seed.json").read_bytes(), (self.config / "seed.json").read_bytes())
        self.assertFalse((case / "execution").exists())
        plan = cli._read(case / "plan.json")
        self.assertNotIn("llm_transport.py", plan["source_sha256"])
        self.assertEqual(cli.generate(case)["calls_permitted"], 0)

    def test_explicit_transport_freezes_optional_dependency(self):
        spec = self.gen_spec()
        value = cli._read(spec)
        value["transport"] = transport()
        self.write(spec, value)
        case = self.base / "explicit"
        cli.prepare_generation(spec, case)
        self.assertIn("llm_transport.py", cli._read(case / "plan.json")["source_sha256"])

    def test_same_spec_prepares_fresh_cases_and_never_overwrites(self):
        spec = self.gen_spec()
        a, b = self.base / "a", self.base / "b"
        cli.prepare_generation(spec, a)
        before = {p.name: p.read_bytes() for p in a.iterdir()}
        with self.assertRaises(FileExistsError): cli.prepare_generation(spec, a)
        self.assertEqual(before, {p.name: p.read_bytes() for p in a.iterdir()})
        cli.prepare_generation(spec, b)
        self.assertEqual(cli._read(a / "plan.json"), cli._read(b / "plan.json"))

    def test_invalid_runner_plan_preserves_failed_preparation_and_cannot_run(self):
        spec = self.gen_spec()
        value = cli._read(spec)
        value["factory_plan"]["max_provider_attempts"] = -1
        self.write(spec, value)
        case = self.base / "failed"
        with self.assertRaises(ValueError): cli.prepare_generation(spec, case)
        self.assertEqual(cli._read(case / "preparation.json")["status"], "failed")
        self.assertFalse((case / "execution").exists())
        with self.assertRaises(ValueError): cli.generate(case, execute=True)

    def test_frozen_input_or_preparation_plan_tampering_rejected(self):
        spec, case = self.gen_spec(), self.base / "tamper"
        cli.prepare_generation(spec, case)
        (case / "seed.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError): cli.generate(case)
        (case / "plan.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError): cli.generate(case)
        self.assertFalse((case / "execution").exists())

    def test_generate_delegates_execute_once_with_existing_budget(self):
        spec, case = self.gen_spec(), self.base / "run"
        cli.prepare_generation(spec, case)
        with patch.object(generation, "run", return_value={"execution": {"status": "completed"}}) as execute:
            cli.generate(case, execute=True)
        execute.assert_called_once_with(case.resolve(), execute=True)

    def test_evaluation_uses_final_version_and_preserves_deleted_original_denominator(self):
        source, report = self.completed_source(drop=True)
        spec, case = self.eval_spec(source), self.base / "evaluation"
        seen = []
        def calibration(evidence, judge, profile):
            seen.append(deepcopy(evidence))
            return {"files": deepcopy(evidence["files"]), "observations": evidence["reviewed_observations"]}
        with patch.object(evaluation, "check_calibration", side_effect=calibration):
            result = cli.prepare_evaluation(spec, case)
        self.assertTrue(result["ready"])
        self.assertEqual((case / "snapshot.json").read_bytes(), (source / "execution/snapshot.json").read_bytes())
        self.assertEqual((case / "review.json").read_bytes(), (source / "execution/phases/final_review/report.json").read_bytes())
        plan = cli._read(case / "plan.json")
        self.assertEqual(plan["lineage"]["original_count"], 2)
        self.assertEqual(plan["lineage"]["current_count"], 1)
        self.assertEqual(plan["lineage"]["dispositions"], report["dispositions"])
        self.assertEqual(plan["max_provider_attempts"], 4)
        self.assertNotIn("status", seen[0])
        self.assertEqual(seen[0]["reviewed_observations"], [{"control": "fixture", "name": "prediction", "verdict": "correct"}])
        self.assertTrue(all(Path(v["path"]).is_absolute() for v in seen[0]["files"].values()))
        self.assertFalse((case / "execution").exists())

    def test_bad_real_calibration_gate_is_not_bypassed_and_leaves_failed_marker(self):
        source, _ = self.completed_source()
        case = self.base / "bad calibration"
        with self.assertRaises(ValueError): cli.prepare_evaluation(self.eval_spec(source), case)
        self.assertEqual(cli._read(case / "preparation.json")["status"], "failed")
        self.assertFalse((case / "execution").exists())

    def test_optional_grading_layout_is_preserved_for_existing_runner(self):
        source, _ = self.completed_source()
        spec = self.eval_spec(source)
        original = cli._read(spec)
        for layout in (None, "strict/v1", evaluation.LAYOUT_VERSION):
            with self.subTest(layout=layout):
                value = deepcopy(original)
                if layout is not None:
                    value["evaluation_plan"]["grading_response_layout"] = layout
                self.write(spec, value)
                case = self.base / ("layout_" + str(layout).replace("/", "_"))
                actual_run = evaluation.run
                observed = []
                def inspect(prepared_case, *, execute):
                    observed.append(cli._read(prepared_case / "plan.json"))
                    return actual_run(prepared_case, execute=execute)
                with patch.object(evaluation, "check_calibration", return_value={"files": {}}), \
                        patch.object(evaluation, "run", side_effect=inspect):
                    result = cli.prepare_evaluation(spec, case)
                self.assertTrue(result["ready"])
                self.assertEqual(len(observed), 1)
                if layout is None:
                    self.assertNotIn("grading_response_layout", observed[0])
                else:
                    self.assertEqual(observed[0]["grading_response_layout"], layout)
                self.assertFalse((case / "execution").exists())

    def test_optional_layout_does_not_allow_unknown_or_missing_settings(self):
        source, _ = self.completed_source()
        spec = self.eval_spec(source)
        original = cli._read(spec)
        for invalid in ("unknown", "missing", "unsupported_layout"):
            with self.subTest(invalid=invalid):
                value = deepcopy(original)
                value["evaluation_plan"]["grading_response_layout"] = evaluation.LAYOUT_VERSION
                if invalid == "unknown": value["evaluation_plan"]["unknown_setting"] = True
                elif invalid == "missing": del value["evaluation_plan"]["judge"]
                else: value["evaluation_plan"]["grading_response_layout"] = "invented/v99"
                self.write(spec, value)
                case = self.base / invalid
                with self.assertRaises(ValueError): cli.prepare_evaluation(spec, case)
                if invalid == "unsupported_layout":
                    self.assertEqual(cli._read(case / "preparation.json")["status"], "failed")
                else:
                    self.assertFalse(case.exists())
                self.assertFalse((case / "execution").exists())

    def test_initial_review_or_original_snapshot_cannot_masquerade_as_final(self):
        source, report = self.completed_source()
        spec = self.eval_spec(source)
        self.write(source / "execution/phases/final_review/report.json", report["phases"]["question_review"])
        with self.assertRaises(ValueError): cli.prepare_evaluation(spec, self.base / "bad review")
        self.assertFalse((self.base / "bad review").exists())
        self.write(source / "execution/phases/final_review/report.json", report["phases"]["final_review"])
        self.write(source / "execution/snapshot.json", report["original_snapshot"])
        with self.assertRaises(ValueError): cli.prepare_evaluation(spec, self.base / "bad snapshot")

    def test_unfinished_source_or_unbound_material_acceptance_cannot_prepare_evaluation(self):
        source, report = self.completed_source()
        spec = self.eval_spec(source)
        for change in ("unfinished", "acceptance"):
            changed = deepcopy(report)
            if change == "unfinished": changed["execution"]["status"] = "running"
            else: changed["material_acceptance"]["corpus_hash"] = "wrong"
            self.write(source / "execution/report.json", changed)
            with self.assertRaises(ValueError): cli.prepare_evaluation(spec, self.base / change)

    def test_evaluate_delegates_once_and_detects_upstream_drift_before_dispatch(self):
        source, _ = self.completed_source()
        case = self.base / "evaluate"
        with patch.object(evaluation, "check_calibration", return_value={"files": {}}):
            cli.prepare_evaluation(self.eval_spec(source), case)
        with patch.object(evaluation, "run", return_value={"execution": {"status": "completed"}}) as execute:
            cli.evaluate(case, execute=True)
            execute.assert_called_once_with(case.resolve(), execute=True)
        (source / "execution/receipt.json").write_text("{}", encoding="utf-8")
        with patch.object(evaluation, "run", side_effect=AssertionError("Must fail before runner")):
            with self.assertRaises(ValueError): cli.evaluate(case, execute=True)

    def test_cli_help_imports_no_provider_configuration(self):
        code = "import sys;from tools.agent_pipeline import main;\ntry: main(['--help'])\nexcept SystemExit: pass\nassert 'config' not in sys.modules"
        result = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "PowerShell launcher integration")
    def test_powershell_preserves_legacy_and_agent_argument_boundaries_and_exit_code(self):
        helper = self.base / "capture args.ps1"
        helper.write_text("ConvertTo-Json -InputObject @($args) -Compress\nexit 7\n", encoding="utf-8")
        original = (ROOT / "run-local.ps1").read_text(encoding="utf-8")
        line = "$taskPython = Join-Path $PSScriptRoot 'venv\\Scripts\\python.exe'"
        self.assertIn(line, original)
        harness = self.base / "launcher.ps1"
        harness.write_text(original.replace(line, "$taskPython = '" + str(helper).replace("'", "''") + "'"), encoding="utf-8")
        for supplied, module, forwarded in [
            (["--scenario", "with spaces", "--tag", "a;b"], "pipeline.factory", ["--scenario", "with spaces", "--tag", "a;b"]),
            (["agent", "generate", "--case", "case path"], "tools.agent_pipeline", ["generate", "--case", "case path"]),
            (["agent"], "tools.agent_pipeline", [])]:
            result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness), *supplied],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual(json.loads(result.stdout.strip()), ["-X", "utf8", "-m", module, *forwarded])


if __name__ == "__main__":
    unittest.main()
