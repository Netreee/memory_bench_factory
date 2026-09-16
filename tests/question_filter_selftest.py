"""离线筛题回归：数据完整性、抽样复现、CLI 及评测完成后的导出。

运行：python -m unittest discover -s tests -p question_filter_selftest.py -v
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import copy
import importlib.util
import io
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from eval.question_filter import export_filtered_benchmark, filter_questions, load_results, question_key
from eval.grading import JUDGE_VERSION


def question(number=0):
    """提供带评分合同和证据引用的独立题目。"""
    return {"qid": f"q{number}", "question": f"问题{number}", "line": "L1_timeline",
            "capability": "IE", "gt": {"value": f"答案{number}"},
            "aux": {"at_week": 2}, "evidence_sessions": ["s1"],
            "strict_scoring": {"required": [f"答案{number}"]}}


def record(q, correct=True, **changes):
    """模拟 harness 逐题判分，不执行模型调用。"""
    grade = {"version": JUDGE_VERSION, "verdict": "correct" if correct is True else "incorrect" if correct is False else "error",
             "correct": correct, "path": "offline_fixture", "reason": "test fixture"}
    return {**copy.deepcopy(q), "pred": "回答", "judgeable": True, "correct": correct, "judgement": grade, **changes}


def set_correct(row, correct):
    row["correct"] = correct
    row["judgement"].update(correct=correct, verdict="correct" if correct else "incorrect")


class QuestionFilterTest(unittest.TestCase):
    def setUp(self):
        self.qs = [question(i) for i in range(10)]
        self.results = {s: [record(q) for q in self.qs] for s in ("A", "B", "C")}

    def test_remove_only_all_correct(self):
        set_correct(self.results["B"][1], False)
        for rows in self.results.values():
            set_correct(rows[2], False)
        kept, report = filter_questions(self.qs, self.results)
        self.assertEqual(kept, self.qs[1:3])
        self.assertEqual(report["counts"]["removed_easy"], 8)
        self.assertEqual(report["counts"]["kept_not_all_correct"], 2)

    def test_ratios_zero_fraction_and_one(self):
        for ratio, count in [(0, 0), (0.29, 2), (0.3, 3), (1, 10)]:
            with self.subTest(ratio=ratio):
                kept, report = filter_questions(self.qs, self.results, keep_easy_ratio=ratio)
                self.assertEqual(len(kept), count)
                self.assertEqual(report["counts"]["kept_easy_sample"], count)

    def test_sampling_is_stable_and_nested(self):
        selected, _ = filter_questions(self.qs, self.results, keep_easy_ratio=0.3, seed=7)
        reversed_results = {s: list(reversed(self.results[s])) for s in reversed(self.results)}
        selected2, _ = filter_questions(list(reversed(self.qs)), reversed_results, keep_easy_ratio=0.3, seed=7)
        more, _ = filter_questions(self.qs, self.results, keep_easy_ratio=0.6, seed=7)
        self.assertEqual({q["qid"] for q in selected}, {q["qid"] for q in selected2})
        self.assertTrue({q["qid"] for q in selected} <= {q["qid"] for q in more})
        self.assertEqual(selected, [q for q in self.qs if q in selected])
        different, _ = filter_questions(self.qs, self.results, keep_easy_ratio=0.3, seed=20)
        self.assertNotEqual(selected, different)

    def test_incomplete_and_failed_judgements_are_retained(self):
        changes = [{"correct": None}, {"correct": "true"}, {"correct": 1},
                   {"error": "timeout"}, {"judge_error": "bad json"}, {"judgeable": False},
                   {"pred": "[SOLVE_ERROR:timeout]"}, {"pred": ""}, {"pred": None}]
        for change in changes:
            with self.subTest(change=change):
                q = question()
                kept, report = filter_questions([q], {"A": [record(q)], "B": [record(q, **change)]})
                self.assertEqual(kept, [q])
                self.assertEqual(report["counts"]["kept_incomplete"], 1)
                self.assertTrue(report["items"][0]["issues"])

    def test_missing_system_row_is_retained_even_when_others_all_correct(self):
        self.results["C"] = []
        kept, report = filter_questions(self.qs, self.results)
        self.assertEqual(kept, self.qs)
        self.assertEqual(report["counts"]["all_correct"], 0)

    def test_duplicate_results_do_not_remove_question(self):
        self.results["A"].append(copy.deepcopy(self.results["A"][0]))
        kept, report = filter_questions(self.qs, self.results)
        self.assertEqual(kept, [self.qs[0]])
        self.assertEqual(report["items"][0]["issues"]["A"], "duplicate_result")

    def test_duplicate_bench_questions_are_retained(self):
        qs = [question(), question()]
        kept, report = filter_questions(qs, {s: [record(qs[0])] for s in ("A", "B")})
        self.assertEqual(kept, qs)
        self.assertEqual(report["counts"]["kept_incomplete"], 2)

    def test_changed_gold_prompt_or_contract_cannot_match_old_result(self):
        for field, changed in [("gt", "new gold"), ("question", "new prompt"),
                               ("aux", {"at_week": 5}), ("strict_scoring", None),
                               ("capability", "L6_refusal"), ("line", "L2_relational")]:
            with self.subTest(field=field):
                q = question()
                old = record(q)
                q[field] = changed
                kept, report = filter_questions([q], {"A": [old], "B": [record(q)]})
                self.assertEqual(kept, [q])
                self.assertEqual(report["unmatched_result_rows"]["A"], 1)

    def test_qid_is_checked_when_present_but_positional_key_is_ignored(self):
        q = question()
        kept, _ = filter_questions([q], {"A": [record(q, qid="different")], "B": [record(q)]})
        self.assertEqual(kept, [q])
        kept, _ = filter_questions([q], {"A": [record(q, key="7:new")], "B": [record(q, key="99:old")]})
        self.assertEqual(kept, [])

    def test_explicit_unjudgeable_bench_is_preserved(self):
        q = {**question(), "judgeable": False}
        kept, _ = filter_questions([q], {s: [record(q)] for s in ("A", "B")})
        self.assertEqual(kept, [q])

    def test_invalid_options_and_single_system_rejected(self):
        for ratio in [-0.01, 1.01, float("nan"), float("inf")]:
            with self.subTest(ratio=ratio), self.assertRaises(ValueError):
                filter_questions(self.qs, self.results, keep_easy_ratio=ratio)
        with self.assertRaises(ValueError):
            filter_questions(self.qs, {"A": self.results["A"]})

    def test_empty_bench_and_no_easy_questions(self):
        kept, report = filter_questions([], {"A": [], "B": []})
        self.assertEqual(kept, [])
        self.assertEqual(report["counts"]["all_correct"], 0)
        for row in self.results["A"]:
            set_correct(row, False)
        kept, _ = filter_questions(self.qs, self.results)
        self.assertEqual(kept, self.qs)

    def test_inputs_are_unchanged(self):
        before = copy.deepcopy((self.qs, self.results))
        filter_questions(self.qs, self.results, keep_easy_ratio=0.3)
        self.assertEqual((self.qs, self.results), before)

    def test_known_bad_judgements_can_be_preserved_for_review(self):
        kept, report = filter_questions(self.qs, self.results, preserve_capabilities=["IE"])
        self.assertEqual(kept, self.qs)
        self.assertEqual(report["counts"]["removed_easy"], 0)
        self.assertEqual(report["items"][0]["issues"]["review"], "capability_pending_review")
        self.assertEqual(report["preserve_capabilities"], ["IE"])

    def test_reports_count_every_question_exactly_once(self):
        set_correct(self.results["A"][0], False)
        self.results["C"].pop()
        _, report = filter_questions(self.qs, self.results, keep_easy_ratio=0.25)
        self.assertEqual(report["counts"], {"input": 10, "kept": 4, "removed_easy": 6,
            "kept_easy_sample": 2, "kept_not_all_correct": 1, "kept_incomplete": 1, "all_correct": 8})
        self.assertEqual(report["by_line"]["L1_timeline"]["kept"], 4)


class FileFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.qs = [question(i) for i in range(5)]
        self.results = {s: [record(q) for q in self.qs] for s in ("A", "B")}
        self.bench = self.base / "06_grounded_questions.json"
        self.bench.write_text(json.dumps({"questions": self.qs}), encoding="utf-8-sig")
        self.aggregate = self.base / "results.json"
        self.aggregate.write_text(json.dumps({"systems": ["A", "B"],
            "results": {s: {"records": rows} for s, rows in self.results.items()}}), encoding="utf-8")
        (self.base / "05_corpus.json").write_bytes(b'{"sessions": []}\n')
        (self.base / "00_about.json").write_text('{"answer_protocol":{"rules":["original"]}}', encoding="utf-8")

    def run_cli(self, *args):
        """用独立 Python 进程验证用户可直接执行的离线命令。"""
        return subprocess.run([sys.executable, "-X", "utf8", "-m", "eval.question_filter",
            "--bench", str(self.bench), "--allow-unverified", *map(str, args)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


class FileAndCliTest(FileFixture):
    def test_unreleased_input_is_blocked_without_explicit_override(self):
        from pipeline.quality import ReleaseError
        out = self.base / "not-released"
        with self.assertRaises(ReleaseError):
            export_filtered_benchmark(self.bench, self.results, out)
        self.assertFalse(out.exists())

    def test_aggregate_cli_exports_reusable_bundle_and_hashes(self):
        before = {p: p.read_bytes() for p in self.base.iterdir()}
        out = self.base / "filtered"
        process = self.run_cli("--results", self.aggregate, "--out-dir", out, "--keep-easy-ratio", "0.4")
        self.assertEqual(process.returncode, 0, process.stderr)
        exported = json.loads((out / "06_grounded_questions.json").read_text(encoding="utf-8"))
        self.assertEqual(len(exported), 2)
        self.assertTrue(all(q in self.qs for q in exported))
        self.assertEqual((out / "05_corpus.json").read_bytes(), before[self.base / "05_corpus.json"])
        self.assertEqual((out / "00_about.json").read_bytes(), before[self.base / "00_about.json"])
        report = json.loads((out / "filter_report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report["input_files"]), 4)
        self.assertTrue(all(len(row["sha256"]) == 64 for row in report["input_files"]))
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_jsonl_cli(self):
        args = []
        for system, rows in self.results.items():
            path = self.base / f"{system}.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8-sig")
            args.extend(["--system-result", f"{system}={path}"])
        out = self.base / "filtered"
        process = self.run_cli(*args, "--out-dir", out)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["removed_easy"], 5)

    def test_explicit_system_subset_and_missing_declared_system(self):
        self.assertEqual(set(load_results(aggregate=self.aggregate, systems=["B", "A"])), {"A", "B"})
        with self.assertRaises(ValueError):
            load_results(aggregate=self.aggregate, systems=["A", "missing"])
        data = json.loads(self.aggregate.read_text())
        data["systems"].append("missing")
        self.aggregate.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            load_results(aggregate=self.aggregate)

    def test_existing_directory_cannot_overwrite_inputs(self):
        before = self.bench.read_bytes()
        with self.assertRaises(FileExistsError):
            export_filtered_benchmark(self.bench, self.results, self.base, allow_unverified=True)
        self.assertEqual(self.bench.read_bytes(), before)

    def test_invalid_ratio_creates_no_output(self):
        out = self.base / "filtered"
        process = self.run_cli("--results", self.aggregate, "--out-dir", out, "--keep-easy-ratio", "nan")
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(out.exists())

    def test_malformed_rows_and_duplicate_system_names_rejected(self):
        with self.assertRaises(ValueError):
            filter_questions(self.qs, {"A": [{"correct": True}], "B": []})
        with self.assertRaises(ValueError):
            load_results(aggregate=self.aggregate, systems=["A", "A"])
        process = self.run_cli("--system-result", f"A={self.aggregate}",
                               "--system-result", f"A={self.aggregate}", "--out-dir", self.base / "out")
        self.assertNotEqual(process.returncode, 0)

    def test_cache_retains_matching_fields(self):
        from eval import qa_cache
        with patch.object(qa_cache, "CACHE_DIR", self.base / "cache"):
            row = record(self.qs[0], _qh=qa_cache.qhash(self.qs[0]))
            qa_cache.append("bench", "A", row)
            loaded = qa_cache.load("bench", "A")[row["_qh"]]
            self.assertEqual(question_key(loaded), question_key(row))
            self.assertEqual(loaded["qid"], row["qid"])


class MultiSystemIntegrationTest(FileFixture):
    def load_harness(self):
        """仅替换不可用的向量后端，评测主流程和导出运行真实代码。"""
        optional = {"eval.memory_interface": SimpleNamespace(EmbedMemory=object, _chunk=Mock()),
                    "eval.embed_cache": SimpleNamespace(cached_embed=Mock(), cache_size=Mock())}
        spec = importlib.util.spec_from_file_location("filter_test_multi_system", ROOT / "eval/multi_system.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, optional):
            spec.loader.exec_module(module)
        return module

    def test_main_exports_after_evaluation_and_ratio_enables_filter(self):
        module = self.load_harness()
        # 一道题不参与评测，确认 smoke/缺测部分仍留在导出题库中。
        scored = self.qs[:-1]
        systems = SimpleNamespace(make_system=Mock(return_value=Mock()))
        argv = ["multi_system", "--bench", str(self.bench), "--corpus", str(self.base / "05_corpus.json"),
                "--systems", "fake1,fake2", "--keep-easy-ratio", "0.5", "--allow-unverified"]
        with patch.dict(sys.modules, {"eval.memory_systems": systems}), \
             patch.object(module, "ROOT", self.base), \
             patch.object(module, "load_corpus", return_value=[("s1", "2026-09-01", "context")]), \
             patch.object(module, "run_system", side_effect=lambda *a, **k: [record(q) for q in scored]), \
             patch.object(module.config, "chat", side_effect=AssertionError("禁止模型调用")), \
             patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
            module.main()
        reports = list(self.base.glob("output/eval/*/filtered/filter_report.json"))
        self.assertEqual(len(reports), 1)
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        self.assertEqual(report["counts"]["removed_easy"], 2)
        self.assertEqual(report["counts"]["kept"], 3)
        self.assertEqual(report["counts"]["kept_incomplete"], 1)
        raw = json.loads((reports[0].parent.parent / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(len(raw["results"]["fake1"]["records"]), 4)

    def test_bad_filter_options_rejected_before_evaluation(self):
        module = self.load_harness()
        with patch.object(sys, "argv", ["multi_system", "--systems", "A", "--filter-easy"]), \
             patch.object(module, "load_corpus") as corpus, \
             patch.object(module, "run_system") as evaluate, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                module.main()
            corpus.assert_not_called()
            evaluate.assert_not_called()

    def test_release_guard_runs_before_solver(self):
        from pipeline.quality import ReleaseError
        module = self.load_harness()
        with patch.object(sys, "argv", ["multi_system", "--bench", str(self.bench), "--corpus", str(self.base / "05_corpus.json")]), \
             patch.object(module, "run_system") as evaluate, patch.object(module, "load_corpus") as corpus:
            with self.assertRaises(ReleaseError):
                module.main()
            evaluate.assert_not_called()
            corpus.assert_not_called()


class DerivedReleaseTest(unittest.TestCase):
    def make_source(self, directory, *, floor=1):
        from pipeline.corpus_contract import review_documents, attach_receipts
        from pipeline.question_contract import attach_question_contract, bind_question_world
        from pipeline.quality import evaluate_release
        from pipeline.world_state import WorldState, Timeline, Op, SET, UPDATE
        blueprint = {"entity_types": [{"id": "record", "fields": [{"name": "状态", "kind": "status"}]}]}
        ws = WorldState({"测试报告": {"状态": Timeline([Op(0, "2025-01-06", SET, "待接收"),
                        Op(1, "2025-01-13", UPDATE, "已登记", "待接收")])}}, n_sessions=2,
                        entity_types={"测试报告": "record"}, world_blueprint=blueprint)
        wp = {"world_blueprint": blueprint, "active_lines": [{"line": "L1_timeline", "weight": 1}]}
        orders = [
            {"capability": "IE", "gt": {"value": "待接收", "at_week": 0},
             "aux": {"at_week": 0, "value": "待接收"}, "evidence_sessions": [0]},
            {"capability": "KU", "gt": "已登记", "aux": {"at_week": 1}, "evidence_sessions": [1]},
        ]
        source, final, sessions = [], [], []
        for order in orders:
            order.update(line="L1_timeline", entity="测试报告", field="状态")
            q = attach_question_contract(bind_question_world(order, ws), wp)
            q["question"] = q["question_contract"]["canonical_question"]
            source.append(q)
            ids = [f"doc{s}" for s in q["evidence_sessions"]]
            final.append({**q, "candidate_evidence_doc_ids": ids, "evidence_doc_ids": ids})
        reviewer = SimpleNamespace(chat_json=Mock(return_value={"verdict": "pass", "unsupported_claims": []}))
        for session, value in enumerate(("待接收", "已登记")):
            docs = [{"doc_id": f"doc{session}", "is_filler": False, "content": f"测试报告的状态为{value}。"}]
            review = review_documents(reviewer, ws, session, docs)
            attach_receipts(docs, review, session)
            sessions.append({"session_id": session, "docs": docs})
        target = {"min_questions": floor, "per_line_min": {"L1_timeline": floor}, "requested_questions": 2}
        artifacts = {"01_whitepaper.json": wp, "02_world.json": ws.to_dict(), "04_questions.json": source,
                     "06_grounded_questions.json": final, "05_corpus.json": {"corpus": {"sessions": sessions}},
                     "00_about.json": {}, "manifest.json": {"status": "done", "algo": {"targetspec": target}}}
        for name, content in artifacts.items():
            (directory / name).write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        receipt = evaluate_release(directory)
        self.assertTrue(receipt["eligible"], receipt["issues"])
        (directory / "07_release.json").write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
        results = {system: [record(final[0]), record(final[1], correct=False)] for system in ("A", "B")}
        return directory / "06_grounded_questions.json", results, target

    def test_valid_subset_has_own_release_and_preserves_bound_inputs(self):
        from pipeline.quality import require_release, INPUTS
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            bench, results, target = self.make_source(source)
            old_receipt = (source / "07_release.json").read_bytes()
            out = source / "filtered"
            report = export_filtered_benchmark(bench, results, out)
            self.assertTrue(require_release(out / "06_grounded_questions.json")["eligible"])
            self.assertEqual(report["result_scope"], "release_eligible")
            self.assertEqual(report["release"]["checks"]["coverage"]["final_count"], 1)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["algo"]["targetspec"], target)
            for name in INPUTS:
                if name != "06_grounded_questions.json":
                    self.assertEqual((out / name).read_bytes(), (source / name).read_bytes())
            self.assertNotEqual((out / "07_release.json").read_bytes(), old_receipt)
            expected_hash = hashlib.sha256(old_receipt).hexdigest()
            self.assertIn({"path": str((source / "07_release.json").resolve()), "sha256": expected_hash},
                          manifest["derived_from"]["input_files"])

    def test_floor_violation_and_empty_subset_fail_release(self):
        from pipeline.quality import require_release, ReleaseError
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            bench, results, target = self.make_source(source, floor=2)
            for empty in (False, True):
                if empty:
                    for rows in results.values():
                        set_correct(rows[1], True)
                out = source / ("empty" if empty else "below-floor")
                report = export_filtered_benchmark(bench, results, out)
                self.assertEqual(report["result_scope"], "filtered_release_failed")
                self.assertFalse(report["release"]["eligible"])
                self.assertIn("empty_filtered_benchmark" if empty else "delivery_target_unmet",
                              {issue["code"] for issue in report["release"]["issues"]})
                manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["algo"]["targetspec"], target)
                with self.assertRaises(ReleaseError):
                    require_release(out / "06_grounded_questions.json")

    def test_research_source_cannot_upgrade_on_export_or_recheck(self):
        from pipeline.quality import require_release, evaluate_release, ReleaseError
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            bench, results, _ = self.make_source(source)
            (source / "07_release.json").unlink()
            out = source / "research"
            report = export_filtered_benchmark(bench, results, out, allow_unverified=True)
            self.assertEqual(report["result_scope"], "research_only")
            self.assertFalse(report["release"]["eligible"])
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["release_policy"]["inherited_research_only"])
            self.assertNotIn(manifest["status"], ("rejected", "manual_failed"))
            repeated = evaluate_release(out)
            self.assertFalse(repeated["eligible"])
            self.assertIn("unverified_source_derivation", {issue["code"] for issue in repeated["issues"]})
            self.assertNotIn("explicit_semantic_rejection", {issue["code"] for issue in repeated["issues"]})
            (out / "07_release.json").write_text(json.dumps(repeated), encoding="utf-8")
            with self.assertRaises(ReleaseError):
                require_release(out / "06_grounded_questions.json")


if __name__ == "__main__":
    unittest.main()
