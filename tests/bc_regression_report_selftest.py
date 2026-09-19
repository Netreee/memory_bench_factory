"""Offline safeguards for the original artifact regression reporter; zero model calls."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workstreams.bc_regression.report import build_report, digest, match_rows, render_html, write_report


class ReporterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.run = self.base / "run"
        self.run.mkdir()
        self.q = {"line": "L1_timeline", "capability": "MR", "entity": "A", "field": "released",
                  "gt": {"value": "2023-08-01"}, "aux": {"ans_kind": "date", "agg": "max"},
                  "evidence_sessions": [0, 1], "question": "<script>alert('x')</script>Latest date?"}
        self.write("00_about.json", {"answer_protocol": {"version": 1}, "secret": "hidden_protocol_secret"})
        self.write("01_whitepaper.json", {"active_lines": [{"line": "L1_timeline"}, {"line": "L7_consolidation"}]})
        self.write("02_world.json", {"entities": {"A": {"released": [{"op": "SET", "value": "2023-08-01"}, {"op": "UPDATE", "value": "2023-09-25"}], "secret": "world_secret"}}})
        self.write("03_orders.json", [{k: v for k, v in self.q.items() if k != "question"}])
        self.write("04_questions.json", [self.q])
        self.write("05_corpus.json", {"corpus": {"sessions": [{"session_id": 0, "date": "2025-01-01", "docs": [
            {"doc_id": "d1", "content": "2023-08-01", "fact_refs": ["world_secret"], "review_receipt": "private_receipt"},
            {"doc_id": "d2", "content": "2023-09-25"}]}]}})
        self.write("06_grounded_questions.json", [{**self.q, "evidence_doc_ids": ["d1", "d2"]}])

    def write(self, name, obj):
        (self.run / name).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    def report(self):
        return build_report(self.run, root=self.base)

    def test_private_fields_do_not_enter_public_projection(self):
        report, public = self.report()
        encoded = json.dumps(public)
        for token in ("private_receipt", "world_secret", "hidden_protocol_secret", "\"gt\"", "fact_refs"):
            self.assertNotIn(token, encoded)
        self.assertEqual(public["documents"][1]["content"], "2023-09-25")
        self.assertEqual(report["questions"][0]["private_reference"]["gt"], {"value": "2023-08-01"})

    def test_independent_date_arithmetic_and_coverage(self):
        report, _ = self.report()
        check = report["questions"][0]["independent_date_arithmetic"]
        self.assertEqual(check["status"], "mismatch")
        self.assertEqual(check["computed_value"], "2023-09-25")
        self.assertEqual(report["summary"]["active_lines_without_final_questions"], ["L7_consolidation"])
        self.assertEqual(report["questions"][0]["public_support_status"], "not_assessed")

    def test_ambiguous_order_join_is_not_silently_resolved(self):
        self.assertEqual(match_rows(self.q, [self.q, deepcopy(self.q)]), {
            "method": "exact_original_spec_fields", "indexes_1based": [1, 2], "status": "ambiguous"})
        changed = {**self.q, "gt": "different"}
        self.assertEqual(match_rows(changed, [self.q])["status"], "missing")

    def test_missing_duplicate_empty_references_are_distinct(self):
        self.write("05_corpus.json", {"sessions": [{"docs": [{"doc_id": "d1", "content": "one"}, {"doc_id": "d1", "content": "two"}]}]})
        self.write("06_grounded_questions.json", [{**self.q, "evidence_doc_ids": ["d1", "missing"]}, {**self.q, "evidence_doc_ids": []}])
        report, _ = self.report()
        row = report["questions"][0]
        self.assertEqual([ref["status"] for ref in row["public_candidate_references"]], ["ambiguous", "missing"])
        self.assertEqual(report["summary"]["uniquely_locatable_reference_questions"], 0)
        self.assertEqual(report["summary"]["nonempty_reference_questions"], 1)

    def test_catalog_indexes_cannot_bind_to_another_snapshot(self):
        self.write("08_semantic_review.json", {"findings": [{"id": "S3_date_max_fake", "06_question_indexes": [1]}]})
        report, _ = self.report()
        self.assertEqual(report["questions"][0]["historical_annotations"], [])
        for case in report["cases"]:
            self.assertEqual(case["matched_report_ids"], [])

    def test_bounded_scope_is_not_treated_as_all_time_max(self):
        self.write("06_grounded_questions.json", [{**self.q, "aux": {**self.q["aux"], "at_week": 0}}])
        report, _ = self.report()
        self.assertEqual(report["questions"][0]["independent_date_arithmetic"]["status"], "needs_review")

    def test_output_does_not_modify_source_and_html_escapes_text(self):
        before = {p.name: digest(p) for p in self.run.iterdir()}
        report, public = self.report()
        with self.assertRaises(ValueError):
            write_report(report, public, self.run / "subdir")
        write_report(report, public, self.base / "report")
        self.assertEqual(before, {p.name: digest(p) for p in self.run.iterdir()})
        rendered = render_html(report)
        self.assertNotIn("<script>alert", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_real_historical_insurance_snapshot(self):
        run = ROOT / "output/runs/seed_insurance_e2e_20260916"
        if not (run / "06_grounded_questions.json").is_file():
            self.skipTest("Historical artifacts not present in this checkout")
        report, public = build_report(run)
        self.assertEqual(report["summary"]["stages"]["questions"]["count"], 166)
        self.assertEqual(report["summary"]["stages"]["final"]["count"], 145)
        self.assertEqual(len(public["questions"]), 145)
        self.assertEqual(report["summary"]["stages"]["final"]["by_line"]["L6_refusal"], 89)
        self.assertEqual(report["summary"]["date_arithmetic_statuses"]["mismatch"], 4)
        self.assertEqual(report["questions"][29]["original_question_match"]["indexes_1based"], [31])
        self.assertEqual(report["cases"][4]["matched_report_ids"], ["final:20", "final:23", "final:27", "final:30"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
