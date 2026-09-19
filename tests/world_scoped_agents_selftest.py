"""Offline scripted agent protocol tests; these do not prove model quality."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from pipeline import disclosure as d, world_semantics as review
from pipeline.world_context import ReadWindow
from world_semantics_selftest import fixture


class ScopedAgentFixture:
    """Reusable by original factory integration tests; exact two legacy steps."""

    def __init__(self, *, reject=False):
        self.calls = []
        self.nodes = {}
        self.reject = reject

    def chat_json(self, step, messages, **params):
        self.calls.append({"step": step, "messages": deepcopy(messages), "params": deepcopy(params)})
        payload = json.loads(messages[-1]["content"])
        self.nodes.setdefault(step, {}).update(payload["exact_reads"])
        pending_parts = {key: [part for part in parts if
                              part != payload["exact_reads"].get(key, {}).get("part")]
                         for key, parts in payload.get("unread_parts", {}).items()}
        remaining = [key for key in payload["unread_ids"]
                     if key not in payload["exact_reads"] or pending_parts.get(key)]
        if remaining:
            if remaining[0] in pending_parts:
                return {"action": "inspect", "ids": [remaining[0]], "part": pending_parts[remaining[0]][0]}
            return {"action": "inspect", "ids": remaining[:8]}
        if step == d.STEP:
            if not payload["working_state"]["records"]:
                refs = payload["requirements"]["refs_requiring_arrangement"]
                return {"action": "arrange", "records": ([{"session": payload["requirements"]["calendar"][-1]["session"],
                    "refs": refs, "channel": "离线场景记录", "acquisition_context": "在本期汇总此前各时点的原始记录。"}] if refs else []),
                    "undisclosed": [], "record_updates": [], "reason": "仅用于接口测试的固定作者响应。"}
            return {"action": "finish", "reason": "离线安排完成；模型语义质量尚未实测。"}
        if step != review.STEP:
            raise AssertionError("Unsupported fixture step: " + step)
        world_refs = [r["ref_id"] for node in self.nodes[step].values() for r in node.get("reference_index", [])]
        record_contexts = [node["value"] for node in self.nodes[step].values()
                           if isinstance(node.get("value"), dict) and "record_ref_id" in node["value"]]
        state = payload["working_state"]
        mechanisms = payload["requirements"]["seed"]["mechanisms"]
        if (len(state["mechanism_coverage"]) < len(mechanisms)
                or len(state["disclosure_reviews"]) < len(record_contexts)
                or self.reject and not state["issues"]):
            return {"action": "submit", "issues": ([{"id": "offline_issue", "finding": "离线注入阻断问题。",
                "refs": [world_refs[0]], "alternative_reading": "本测试没有足以消除该问题的证据。", "disposition": "unresolved"}] if self.reject else []),
                "mechanism_coverage": [{"mechanism_id": m["id"], "status": "witnessed", "refs": [world_refs[0]],
                    "observed_sequence": "离线假意见仅验证引用与控制流。", "reason": "此意见不作为业务质量结论。"} for m in mechanisms],
                "disclosure_reviews": [{"record_id": r["record_id"], "understanding": "离线固定记录理解。",
                    "refs": [r["record_ref_id"]], "reason": "只验证已读取并逐条提交。", "status": "compatible"} for r in record_contexts],
                "note": "读取完毕，保留原始节点的引用。"}
        targets = {"intrinsic": [], "structure": False}
        if payload["requirements"].get("public_disclosure_scope"):
            targets["disclosure"] = False
        return {"action": "finish", "decision": "unresolved" if self.reject else "accept", "reason": "离线固定最终意见。",
                "limitations": "本轮只验证工程协议；没有调用真实模型。", "repair_targets": targets}


class Tests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")).start()
        patch.dict(sys.modules, {"config": SimpleNamespace(STRUCTURE_MODEL="cheap-author", REVIEWER_MODEL="cheap-review")}).start()
        self.wp, self.ws, self.task = fixture()
        self.wp["world_generation"] = {"strategy": "agentic"}
        self.wp["quality_contract"]["public_disclosure"] = True
        self.ws.conflicts = []

    def author(self):
        tracer = ScopedAgentFixture()
        report = d.author_plan(self.wp, self.ws, tracer, self.task)
        self.assertEqual(report["status"], "ready", report)
        self.assertEqual(d.validate_plan(self.ws), [])
        return tracer, report

    def test_original_author_compiler_and_review_parser_end_to_end(self):
        tracer, report = self.author()
        result = review.review_world(self.wp, self.ws, tracer, self.task)
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(review.validate_review(result, self.wp, self.ws, self.task), [])
        self.assertGreater(report["logical_calls"], 1)
        self.assertGreater(result["logical_calls"], 1)
        for call in tracer.calls:
            payload = json.loads(call["messages"][-1]["content"])
            self.assertNotIn("world", payload["requirements"])
            self.assertNotIn("catalogue", payload["requirements"])

    def test_disclosure_exact_read_tampering_rejected_even_with_new_plan_hash(self):
        self.author()
        self.ws.disclosure["transcript"][1]["messages_hash"] = "0" * 64
        self.ws.disclosure["plan_hash"] = d._hash({k: v for k, v in self.ws.disclosure.items() if k != "plan_hash"})
        self.assertTrue(d.validate_plan(self.ws))

    def test_disclosure_original_fact_or_action_edits_invalidate_replay(self):
        self.author()
        original = deepcopy(self.ws.disclosure)
        self.ws.disclosure["transcript"][0]["raw_output"]["ids"] = []
        self.assertTrue(d.validate_plan(self.ws))
        self.ws.disclosure = original
        self.ws.disclosure["author_input"]["catalogue"][0]["value"] = "tampered original"
        self.ws.disclosure["plan_hash"] = d._hash({k: v for k, v in self.ws.disclosure.items() if k != "plan_hash"})
        self.assertTrue(d.validate_plan(self.ws))

    def test_review_action_and_exact_read_hash_edits_invalidate_replay(self):
        self.author()
        original = review.review_world(self.wp, self.ws, ScopedAgentFixture(), self.task)
        self.assertEqual(original["status"], "passed", original)
        for kind in ("hash", "action", "source"):
            report = deepcopy(original)
            if kind == "hash":
                report["transcript"][1]["messages_hash"] = "0" * 64
            elif kind == "action":
                report["transcript"][0]["raw_output"]["ids"] = []
            else:
                report["input_snapshot"]["calendar"][0]["date"] = "2099-01-01"
            self.assertTrue(review.validate_review(report, self.wp, self.ws, self.task), kind)

    def test_reviewer_receipt_replay_and_negative_preserved(self):
        self.author()
        report = review.review_world(self.wp, self.ws, ScopedAgentFixture(reject=True), self.task)
        self.assertEqual(report["status"], "unresolved", report)
        self.assertEqual(review.validate_review(report, self.wp, self.ws, self.task)[0]["code"], "world_review_not_passed")
        report["status"] = "passed"
        self.assertEqual(review.validate_review(report, self.wp, self.ws, self.task)[0]["code"], "missing_or_stale_world_review")

    def test_cannot_finish_without_reading_or_silently_hide_missing_refs(self):
        class Premature:
            def __init__(self): self.calls = 0
            def chat_json(self, step, messages, **params):
                self.calls += 1
                return {"action": "finish", "reason": "省略读取"}
        tracer = Premature()
        report = d.author_plan(self.wp, self.ws, tracer, self.task)
        self.assertEqual(report["status"], "error")
        self.assertEqual(tracer.calls, 4)
        self.assertFalse(self.ws.disclosure)
        self.author()
        tracer = Premature()
        report = review.review_world(self.wp, self.ws, tracer, self.task)
        self.assertEqual(report["status"], "error")
        self.assertEqual(tracer.calls, 4)
        self.assertIn("reading every", report["error"])

    def test_cannot_arrange_unread_fact_and_can_recover_with_actual_read(self):
        class Recover(ScopedAgentFixture):
            def chat_json(self, step, messages, **params):
                if not self.calls:
                    self.calls.append({})
                    return {"action": "arrange", "records": [], "undisclosed": ["f1"], "reason": "未经读取的决定。"}
                return super().chat_json(step, messages, **params)
        report = d.author_plan(self.wp, self.ws, Recover(), self.task)
        self.assertEqual(report["status"], "ready", report)
        self.assertEqual(self.ws.disclosure["undisclosed"], [])
        self.assertEqual(d.validate_plan(self.ws), [])

    def test_provider_sentinel_stops_without_semantic_retry(self):
        class Broken:
            def __init__(self): self.calls = 0
            def chat_json(self, *args, **kwargs):
                self.calls += 1
                return {"__error__": "offline timeout"}
        tracer = Broken()
        result = d.author_plan(self.wp, self.ws, tracer, self.task)
        self.assertEqual(result["status"], "error")
        self.assertEqual(tracer.calls, 1)
        self.assertEqual(result["logical_calls"], 1)

    def test_inspect_is_not_counted_until_original_content_is_sent(self):
        window = ReadWindow({}, {"a": {"label": "a", "value": "actual"}})
        window.control({"action": "inspect", "ids": ["a"]})
        self.assertEqual(window.read, set())
        self.assertIn("actual", window.messages("test", {})[-1]["content"])
        window.mark_sent()
        self.assertEqual(window.read, {"a"})

    def test_read_window_rejects_oversized_selection_without_losing_prior_state(self):
        window = ReadWindow({}, {key: {"label": key, "value": "x" * 13000} for key in ("a", "b")})
        with self.assertRaises(ValueError):
            window.control({"action": "inspect", "ids": ["a", "b"]})
        self.assertEqual(window.read, set())
        window.control({"action": "inspect", "ids": ["a"]})
        self.assertEqual(set(window.visible), {"a"})

    def test_large_original_node_pages_preserve_exact_bytes_and_require_all_reads(self):
        original = {"label": "large", "value": "原始资料。" * 11000}
        window = ReadWindow({}, {"large": original})
        fragments = []
        count = window.parts("large")
        for part in range(count):
            window.control({"action": "inspect", "ids": ["large"], "part": part})
            window.messages("offline", {})
            fragments.append(window.visible["large"]["serialized_json_fragment"])
            self.assertNotIn("large", window.read)
            window.mark_sent()
        self.assertEqual(json.loads("".join(fragments)), original)
        self.assertEqual(window.read, {"large"})

    def test_input_room_checked_before_accepting_next_inspection(self):
        window = ReadWindow({"frozen": "x" * 108000}, {"a": {"value": "x" * 15000}})
        window.messages("offline", {})
        with self.assertRaisesRegex(ValueError, "total input"):
            window.control({"action": "inspect", "ids": ["a"]})
        self.assertEqual(window.visible, {})

    def test_raised_provider_error_records_entered_call(self):
        class Broken:
            def chat_json(self, *args, **kwargs):
                raise TimeoutError("local test deadline")
        result = d.author_plan(self.wp, self.ws, Broken(), self.task)
        self.assertTrue(result["caller_entered"])
        self.assertEqual(result["logical_calls"], 1)
        self.assertEqual(result["attempts"][0]["error_type"], "TimeoutError")

    def test_feedback_omits_repeated_prompt_transcript_preserves_opinion(self):
        feedback = {"version": review.VERSION, "raw_output": {"reason": "原业务意见"},
                    "locations": {"issues": [{"source_value": "原事实"}]},
                    "transcript": [{"messages": "huge repeated inputs"}]}
        projected = d._feedback_projection(feedback)
        self.assertNotIn("transcript", projected)
        self.assertEqual(projected["raw_output"], feedback["raw_output"])
        self.assertEqual(projected["locations"], feedback["locations"])


if __name__ == "__main__":
    unittest.main()
