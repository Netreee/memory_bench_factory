"""Offline artifact integration checks, using completed case1 only.

No provider or network; all mutations/exports live in TemporaryDirectory.
"""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import hashlib
import importlib.abc
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
ROOT = next(p for p in (HERE, *HERE.parents) if (p / "tools/run_agent_evaluation.py").is_file())
BASE = Path(os.environ.get("AGENT_ARTIFACT_CASE1_BASE", ROOT / "output/implementation_bc_agent_loop_20260917"))
sys.path.insert(0, str(ROOT))


class NoProviderImport(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split(".")[0] in {"config", "openai"}:
            raise AssertionError("Read-only adapter imported provider/config: " + fullname)


sys.meta_path.insert(0, NoProviderImport())
socket.socket = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("Network disabled"))


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


adapter = module("pipeline.agent_artifacts", HERE / "pipeline/agent_artifacts.py")
GEN = BASE / "prospective_acceptance1"
EVAL = BASE / "prospective_acceptance1_evaluation"
AUDIT = EVAL / "audited_view/report.json"
GEN_FILES = ("execution/report.json", "execution/original_snapshot.json", "execution/snapshot.json",
             "execution/receipt.json", "execution/phases/final_review/report.json")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def clone_generation(target):
    for name in GEN_FILES:
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(GEN / name, dest)
    return target


def clone_evaluation(target):
    for name in ("plan.json", "snapshot.json", "review.json", "calibration.json",
                 "execution/report.json", "execution/manifest.json", "execution/attempts.jsonl", "execution/calls.jsonl"):
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(EVAL / name, dest)
    for name in ("execution/inputs", "execution/source_snapshot", "execution/reservations", "execution/items"):
        shutil.copytree(EVAL / name, target / name)
    return target


class ArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not all((GEN / name).is_file() for name in GEN_FILES) or not (EVAL / "execution/report.json").is_file():
            raise unittest.SkipTest("Read-only integration fixture case1 is absent; set AGENT_ARTIFACT_CASE1_BASE to a real completed case1 artifact base. No success is inferred from missing evidence.")
        cls.before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in [*(GEN / n for n in GEN_FILES), EVAL / "execution/report.json", AUDIT]}
        extra = [*(BASE / "prospective_acceptance2" / n for n in GEN_FILES),
                 BASE / "prospective_acceptance2_evaluation/execution/report.json"]
        cls.before.update({str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in extra if p.is_file()})
        cls.view = adapter.read_agent_dataset(GEN, evaluation_case=EVAL)

    @classmethod
    def tearDownClass(cls):
        assert cls.before == {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in cls.before}
        assert "config" not in sys.modules and "openai" not in sys.modules

    def test_real_generation_and_evaluation_denominators(self):
        v = self.view
        self.assertEqual(v["denominators"]["original_count"], 4)
        self.assertEqual(v["denominators"]["current_count"], 4)
        self.assertEqual(len(v["public"]["documents"]), 12)
        self.assertEqual(len(v["public"]["questions"]), 4)
        self.assertTrue(all(set(q) == {"qid", "question"} for q in v["public"]["questions"]))
        self.assertTrue(all(set(d) <= {"doc_id", "content", "date", "session"} for d in v["public"]["documents"]))
        e = v["evaluation"]
        self.assertEqual(e["ledger"]["request_attempt_events"], 24)
        self.assertEqual(e["ledger"]["response_events"], 24)
        self.assertEqual([(x["original_slots"], x["scored"], x["correct"]) for x in e["summary"].values()], [(4, 4, 4), (4, 4, 1)])
        self.assertTrue(e["filter_verified"])
        self.assertEqual(len(e["retained_qids"]), 3)
        self.assertFalse(v["validation"]["semantic_correctness_verified"])

    def test_exports_all_and_retained_are_source_views(self):
        with tempfile.TemporaryDirectory(prefix="agent_artifacts_") as tmp:
            for selection, count in (("all", 4), ("retained", 3)):
                target = Path(tmp) / selection
                adapter.export_agent_dataset(GEN, target, evaluation_case=EVAL, selection=selection)
                loaded = adapter.read_exported_dataset(target)
                self.assertEqual(len(loaded["public"]["questions"]), count)
                self.assertEqual(loaded["denominators"]["original_count"], 4)
                self.assertEqual(loaded["manifest"]["source_snapshot_id"], self.view["source_snapshot_id"])
                self.assertFalse(loaded["read_validation"]["execution_revalidated"])
                self.assertEqual((target / "audit/source_snapshot.json").read_bytes(), (GEN / "execution/snapshot.json").read_bytes())
                self.assertFalse((target / "audit/source_report.json").exists())
                self.assertEqual((target / "public/protocol.txt").read_bytes(), self.view["public"]["protocol"].encode())

    def test_external_hold_is_linked_opinion_not_a_score_override(self):
        v = adapter.read_agent_dataset(GEN, evaluation_case=EVAL, audited_view=AUDIT)
        self.assertEqual(v["evaluation"]["summary"], self.view["evaluation"]["summary"])
        self.assertFalse(v["evaluation"]["external_opinion"]["applied_to_scores_or_filter"])
        self.assertTrue(v["evaluation"]["external_opinion"]["declared_holds"])
        with self.assertRaisesRegex(ValueError, "does not apply"):
            adapter.read_agent_dataset(GEN, evaluation_case=EVAL, audited_view=AUDIT, selection="retained")
        with tempfile.TemporaryDirectory(prefix="agent_artifacts_") as tmp:
            wrong = read(AUDIT); wrong["automated_source"]["sha256"] = "0" * 64
            path = Path(tmp) / "opinion.json"; write(path, wrong)
            with self.assertRaisesRegex(ValueError, "does not bind"):
                adapter.read_agent_dataset(GEN, evaluation_case=EVAL, audited_view=path)

    def test_changed_source_snapshot_is_not_accepted(self):
        with tempfile.TemporaryDirectory(prefix="agent_artifacts_") as tmp:
            gen = clone_generation(Path(tmp) / "gen")
            snap = read(gen / "execution/snapshot.json")
            snap["questions"][0]["question"] += " altered"
            write(gen / "execution/snapshot.json", snap)
            result = adapter.inspect_agent_case(gen)
            self.assertFalse(result["export_ready"])
            self.assertEqual(result["status"], "unverified_or_incomplete")

    def test_strict_external_hold_preserves_grades_and_filters_effective_rows(self):
        v = adapter.read_agent_dataset(GEN, evaluation_case=EVAL, audited_view=AUDIT, audit_mode="held", selection="retained")
        e = v["evaluation"]
        self.assertEqual(e["view"], "externally_held_view")
        self.assertEqual([(x["scored"], x["correct"], x["external_held"]) for x in e["summary"].values()], [(3, 3, 1), (3, 1, 1)])
        self.assertEqual(e["raw_view"]["summary"], self.view["evaluation"]["summary"])
        self.assertEqual(e["filter"]["counts"]["kept_incomplete"], 1)
        with tempfile.TemporaryDirectory(prefix="agent_artifacts_") as tmp:
            out = Path(tmp) / "export"
            adapter.export_agent_dataset(GEN, out, evaluation_case=EVAL, audited_view=AUDIT, audit_mode="held", selection="retained")
            self.assertEqual(adapter.read_exported_dataset(out)["evaluation"]["view"], "externally_held_view")
            wrong = read(AUDIT)
            wrong["results"]["fullcontext_gpt"]["records"][0]["pred"] += " altered"
            path = Path(tmp) / "opinion.json"; write(path, wrong)
            with self.assertRaisesRegex(ValueError, "more than"):
                adapter.read_agent_dataset(GEN, evaluation_case=EVAL, audited_view=path, audit_mode="held")

    def test_aggregate_model_identity_and_recomputed_grade_cannot_override_items(self):
        from eval.question_filter import filter_questions
        with tempfile.TemporaryDirectory(prefix="agent_artifacts_") as tmp:
            ev = clone_evaluation(Path(tmp) / "eval")
            original = read(ev / "execution/report.json")
            for mutation in ("identity", "grade"):
                report = deepcopy(original)
                row = report["results"]["fullcontext_glm"]["records"][0]
                if mutation == "identity": row["solver_identity"]["model"] = "different-model"
                else:
                    row["correct"] = True
                    row["judgement"].update(verdict="correct", correct=True, reason="synthetic replacement")
                kept, filtered = filter_questions(read(ev / "snapshot.json")["questions"],
                    {name: value["records"] for name, value in report["results"].items()},
                    keep_easy_ratio=0, seed=read(ev / "plan.json")["filter_seed"], expected_context=self.view["evaluation"]["context"])
                report.update(kept_questions=kept, filter=filtered)
                write(ev / "execution/report.json", report)
                with self.assertRaisesRegex(ValueError, "Aggregate results"):
                    adapter.read_agent_dataset(GEN, evaluation_case=ev)

    def test_bundle_changed_snapshot_and_public_with_old_identity_rejected(self):
        with tempfile.TemporaryDirectory(prefix="agent_artifacts_") as tmp:
            target = Path(tmp) / "export"
            adapter.export_agent_dataset(GEN, target)
            paths = [target / "audit/source_snapshot.json", target / "public/questions.json"]
            snap, public = map(read, paths)
            snap["questions"][0]["question"] = public[0]["question"] = "SYNTHETIC altered question"
            manifest = read(target / "manifest.json")
            for path, value in zip(paths, (snap, public)):
                write(path, value)
                manifest["files"][path.relative_to(target).as_posix()] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
            write(target / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "Snapshot content/identity mismatch"):
                adapter.read_exported_dataset(target)

    def test_external_hold_cannot_be_one_sided_or_promote_a_grade(self):
        with tempfile.TemporaryDirectory(prefix="agent_artifacts_") as tmp:
            original = read(AUDIT)
            raw = read(EVAL / "execution/report.json")
            for mutation in ("one_system", "grade"):
                opinion = deepcopy(original)
                row = opinion["results"]["fullcontext_glm"]["records"][0]
                if mutation == "one_system": opinion["results"]["fullcontext_glm"]["records"][0] = deepcopy(raw["results"]["fullcontext_glm"]["records"][0])
                else: row["judgement"].update(verdict="correct", correct=True)
                path = Path(tmp) / (mutation + ".json"); write(path, opinion)
                with self.assertRaisesRegex(ValueError, "more than"):
                    adapter.read_agent_dataset(GEN, evaluation_case=EVAL, audited_view=path, audit_mode="held")

    def test_original_item_tampering_is_detected_against_actual_wire(self):
        with tempfile.TemporaryDirectory(prefix="agent_artifacts_") as tmp:
            ev = clone_evaluation(Path(tmp) / "eval")
            item = read(ev / "execution/items/001_1.json")
            item["pred"] += " synthetic replacement"
            write(ev / "execution/items/001_1.json", item)
            with self.assertRaisesRegex(ValueError, "actual provider response"):
                adapter.read_agent_dataset(GEN, evaluation_case=ev)

    def test_wire_and_reservation_cannot_self_authorize_another_role_model(self):
        from tools import run_agent_evaluation as runner
        from llm_transport import resolve_profile, build_request_parameters
        with tempfile.TemporaryDirectory(prefix="agent_artifacts_") as tmp:
            ev = clone_evaluation(Path(tmp) / "eval")
            directory = ev / "execution"; plan = read(ev / "plan.json")
            reservation = read(directory / "reservations/001.json")
            wrong_model = plan["systems"][1]["model"]
            reservation["params"]["model"] = wrong_model
            write(directory / "reservations/001.json", reservation)
            events = [json.loads(line) for line in (directory / "attempts.jsonl").read_text(encoding="utf-8").splitlines()]
            request = next(e for e in events if e.get("event") == "request")
            profile = resolve_profile(plan["transport"], wrong_model)
            params = reservation["params"]
            wire = build_request_parameters(model=wrong_model, max_tokens=params["max_tokens"], temperature=params["temperature"], top_p=params.get("top_p", 1.0), transport=plan["transport"])
            wire["deadline_s"] = profile["profile"]["deadline_seconds"]
            request.update(model=wrong_model, parameters=wire, transport=profile)
            (directory / "attempts.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
            report = read(directory / "report.json")
            report["ledger"] = runner.dispatch_ledger(directory, plan, [read(p) for p in sorted((directory / "reservations").glob("*.json"))])
            self.assertTrue(report["ledger"]["all_observed_wires_match"])
            write(directory / "report.json", report)
            with self.assertRaisesRegex(ValueError, "role model/token"):
                adapter.read_agent_dataset(GEN, evaluation_case=ev)

    def test_strict_provider_json_compatibility_without_config_import(self):
        value = {"answer": "natural language"}
        text = json.dumps(value)
        for raw in (text, " \n" + text + "\n", "```json\n" + text + "\n```", "```" + text + "``` trailing"):
            with self.subTest(raw=raw): self.assertEqual(adapter._provider_json(raw), value)
        for raw in (text + " trailing", "{broken", "prefix " + text):
            with self.subTest(raw=raw), self.assertRaises(ValueError): adapter._provider_json(raw)

    def test_valid_fenced_actual_response_is_not_rejected(self):
        with tempfile.TemporaryDirectory(prefix="agent_artifacts_") as tmp:
            ev = clone_evaluation(Path(tmp) / "eval")
            path = ev / "execution/attempts.jsonl"
            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            response = next(e for e in events if e.get("event") == "response")
            content = response["response"]["choices"][0]["content"]
            response["response"]["choices"][0]["content"] = "```json\n" + content + "\n```"
            path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
            view = adapter.read_agent_dataset(GEN, evaluation_case=ev)
            self.assertEqual(view["evaluation"]["summary"], self.view["evaluation"]["summary"])

    def test_real_failed_case2_keeps_twelve_slots_without_filter(self):
        generation = BASE / "prospective_acceptance2"
        evaluation = BASE / "prospective_acceptance2_evaluation"
        if not (evaluation / "execution/report.json").exists():
            self.skipTest("The actual terminal case2 failure fixture is absent; no success inferred.")
        view = adapter.read_agent_dataset(generation, evaluation_case=evaluation)
        measured = view["evaluation"]
        self.assertEqual(measured["execution"]["status"], "failed")
        self.assertEqual(measured["ledger"]["request_attempt_events"], 22)
        self.assertEqual(measured["ledger"]["response_events"], 21)
        self.assertEqual(len(measured["slots"]), 12)
        self.assertEqual(sum(x["scored"] for x in measured["summary"].values()), 7)
        self.assertEqual(sum(x["unscored"] for x in measured["summary"].values()), 5)
        self.assertEqual(sum(x["execution_errors"] for x in measured["summary"].values()), 1)
        self.assertEqual(sum(x["incomplete_or_skipped"] for x in measured["summary"].values()), 4)
        self.assertFalse(measured["filter_verified"])
        self.assertIsNone(measured["filter"])
        with self.assertRaisesRegex(ValueError, "complete, verified"):
            adapter.read_agent_dataset(generation, evaluation_case=evaluation, selection="retained")
        with tempfile.TemporaryDirectory(prefix="agent_artifacts_") as tmp:
            out = Path(tmp) / "failed-research"
            adapter.export_agent_dataset(generation, out, evaluation_case=evaluation)
            loaded = adapter.read_exported_dataset(out)
            self.assertEqual(loaded["denominators"]["original_count"], 6)
            self.assertEqual(loaded["evaluation"]["execution"]["status"], "failed")
            self.assertFalse((out / "evaluation/filter.json").exists())
            self.assertTrue(adapter.inspect_agent_case(generation, evaluation_case=evaluation)["export_ready"])

    def test_saved_filter_cannot_override_real_record_filter(self):
        with tempfile.TemporaryDirectory(prefix="agent_artifacts_") as tmp:
            ev = clone_evaluation(Path(tmp) / "eval")
            report = read(ev / "execution/report.json")
            report["kept_questions"] = []
            write(ev / "execution/report.json", report)
            with self.assertRaisesRegex(ValueError, "saved filter"):
                adapter.read_agent_dataset(GEN, evaluation_case=ev)

    def test_trace_loss_cannot_be_hidden_by_saved_ledger(self):
        with tempfile.TemporaryDirectory(prefix="agent_artifacts_") as tmp:
            ev = clone_evaluation(Path(tmp) / "eval")
            path = ev / "execution/attempts.jsonl"
            lines = path.read_text(encoding="utf-8").splitlines(True)
            for i, line in enumerate(lines):
                if json.loads(line).get("event") == "response":
                    del lines[i]; break
            path.write_text("".join(lines), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ledger differs"):
                adapter.read_agent_dataset(GEN, evaluation_case=ev)

    def test_missing_evaluation_and_partial_source_are_not_zero_scores(self):
        view = adapter.read_agent_dataset(GEN)
        self.assertIsNone(view["evaluation"])
        with self.assertRaisesRegex(ValueError, "retained requires"):
            adapter.read_agent_dataset(GEN, selection="retained")
        with tempfile.TemporaryDirectory(prefix="agent_artifacts_") as tmp:
            case = Path(tmp)
            write(case / "execution/failed_report.json", {"version": "agent-case-factory/v2", "execution": {"status": "failed"}})
            result = adapter.inspect_agent_case(case)
            self.assertFalse(result["export_ready"])
            self.assertEqual(result["declared_state"]["execution"]["status"], "failed")
            self.assertNotIn("evaluation", result)

    def test_source_dirs_and_existing_output_are_immutable(self):
        for target in (GEN, GEN / "forbidden", EVAL / "forbidden"):
            with self.assertRaisesRegex(ValueError, "new directory"):
                adapter.export_agent_dataset(GEN, target, evaluation_case=EVAL)
        with tempfile.TemporaryDirectory(prefix="agent_artifacts_") as tmp:
            with self.assertRaisesRegex(ValueError, "new directory"):
                adapter.export_agent_dataset(GEN, tmp)

    def test_write_failure_leaves_explicit_failed_manifest(self):
        with tempfile.TemporaryDirectory(prefix="agent_artifacts_") as tmp:
            target = Path(tmp) / "export"
            original = adapter._write
            def broken(path, value):
                if path.name == "questions.json": raise OSError("injected disk failure")
                return original(path, value)
            with patch.object(adapter, "_write", broken), self.assertRaises(OSError):
                adapter.export_agent_dataset(GEN, target)
            state = read(target / "manifest.json")
            self.assertEqual(state["status"], "failed")
            self.assertFalse(state["ready"])
            with self.assertRaisesRegex(ValueError, "incomplete"):
                adapter.read_exported_dataset(target)

    def test_bundle_projection_cannot_leak_author_fields_even_with_rehashed_file(self):
        with tempfile.TemporaryDirectory(prefix="agent_artifacts_") as tmp:
            target = Path(tmp) / "export"
            adapter.export_agent_dataset(GEN, target)
            path = target / "public/questions.json"
            public = read(path); public[0]["reference_proposal"] = {"answer": "HIDDEN"}
            write(path, public)
            manifest = read(target / "manifest.json")
            manifest["files"]["public/questions.json"] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
            write(target / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "projection differs"):
                adapter.read_exported_dataset(target)

    def test_current_source_or_runtime_mismatch_is_explicit(self):
        from tools import run_agent_evaluation
        with patch.object(run_agent_evaluation, "_prepare", side_effect=ValueError("current source/runtime differs")):
            result = adapter.inspect_agent_case(GEN, evaluation_case=EVAL)
            self.assertFalse(result["export_ready"])
            self.assertEqual(result["validation_error"]["message"], "current source/runtime differs")
            self.assertNotIn("evaluation", result)

    def test_candidate_cli_reuses_reader_and_preserves_existing_commands(self):
        cli = module("staged_agent_pipeline", HERE / "tools/agent_pipeline.py")
        cli.ROOT = ROOT
        with redirect_stdout(io.StringIO()) as output:
            status = cli.main(["inspect", "--case", str(GEN)])
        self.assertEqual(status, 0)
        self.assertTrue(json.loads(output.getvalue())["export_ready"])
        with tempfile.TemporaryDirectory(prefix="agent_artifacts_") as tmp:
            with redirect_stdout(io.StringIO()) as output:
                status = cli.main(["export", "--case", str(GEN), "--out", str(Path(tmp) / "data")])
            self.assertEqual(status, 0)
            self.assertTrue(json.loads(output.getvalue())["ready"])
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(cli.main(["inspect", "--case", tmp]), 1)
            self.assertFalse(json.loads(output.getvalue())["export_ready"])
        with patch.object(cli, "generate", return_value={"execution": {"status": "not_started"}}) as old:
            with redirect_stdout(io.StringIO()): cli.main(["generate", "--case", str(GEN)])
            old.assert_called_once_with(GEN, execute=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
