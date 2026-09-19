"""Offline exact-prefix replay checks; no network or provider configuration."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.provenance import digest
from pipeline.material_replay import MaterialReplay, ReplayMismatchError
from pipeline.material_series import generate_material_series


def entry(index=0):
    return {"step": "material_proposals.batch",
        "messages": [{"role": "system", "content": "immutable instructions"},
                     {"role": "user", "content": json.dumps({"batch": index, "prior": "完整前文"}, ensure_ascii=False)}],
        "params": {"model": "offline", "max_tokens": 8000, "temperature": 0.7,
                   "strict_json": True, "retries": 1},
        "output": {"content": f"原始正文{index}", "notes": ["可错的作者说明"]},
        "provenance": {"source_case": "frozen-test-source", "source_index": index,
                       "operation": "exact_original_output"}}


def invoke(adapter, value):
    return adapter(value["step"], deepcopy(value["messages"]), **deepcopy(value["params"]))


class ReplayTests(unittest.TestCase):
    def test_exact_prefix_then_live_counts_and_provenance_are_separate(self):
        entries, live_calls, records = [entry(0), entry(1)], [], []
        def live(step, messages, **params):
            live_calls.append((step, messages, params))
            return {"new": "live-output"}
        replay = MaterialReplay(entries, live, record=records.append)
        for value in entries:
            self.assertEqual(invoke(replay, value), value["output"])
        self.assertEqual(live_calls, [])
        self.assertEqual(invoke(replay, entry(2)), {"new": "live-output"})
        self.assertEqual(len(live_calls), 1)
        summary = replay.summary()
        self.assertEqual((summary["total"], summary["consumed"], summary["remaining"]), (2, 2, 0))
        self.assertEqual((summary["replayed_calls"], summary["real_calls"]), (2, 1))
        self.assertEqual(records[0]["provenance"], entries[0]["provenance"])
        self.assertEqual(records[0]["output_hash"], digest(entries[0]["output"]))
        self.assertFalse(records[0]["real_provider_call"])
        self.assertEqual([r["event"] for r in records], ["replay", "replay", "live_started", "live_returned"])

    def test_all_request_components_are_exact_and_mismatch_never_falls_back(self):
        variants = []
        step_changed = entry(); step_changed["step"] = "other_step"; variants.append(step_changed)
        text_changed = entry(); text_changed["messages"][1]["content"] += " "; variants.append(text_changed)
        messages_changed = entry(); messages_changed["messages"].reverse(); variants.append(messages_changed)
        for key, value in (("model", "other"), ("max_tokens", 7999), ("retries", 2),
                           ("strict_json", 1), ("temperature", 0)):
            changed = entry(); changed["params"][key] = value; variants.append(changed)
        extra = entry(); extra["params"]["new_setting"] = True; variants.append(extra)
        for changed in variants:
            with self.subTest(changed=changed):
                calls = []
                replay = MaterialReplay([entry()], lambda *a, **k: calls.append(True))
                with self.assertRaises(ReplayMismatchError):
                    invoke(replay, changed)
                self.assertEqual(calls, [])
                self.assertEqual(replay.summary()["consumed"], 0)
                self.assertEqual(replay.summary()["mismatch_count"], 1)
                with self.assertRaises(ReplayMismatchError):
                    invoke(replay, entry())
                self.assertEqual(calls, [])

    def test_duplicate_or_reordered_prefix_is_not_silently_selected(self):
        with self.assertRaises(ValueError):
            MaterialReplay([entry(), entry()], lambda *a, **k: None)
        calls = []
        replay = MaterialReplay([entry(1), entry(0)], lambda *a, **k: calls.append(True))
        with self.assertRaises(ReplayMismatchError):
            invoke(replay, entry(0))
        self.assertEqual(calls, [])
        self.assertEqual(replay.summary()["consumed"], 0)

    def test_inputs_returned_outputs_and_record_views_are_detached(self):
        original, entries = entry(), [entry()]
        seen = []
        def recorder(event):
            seen.append(deepcopy(event))
            event["provenance"].clear()
        replay = MaterialReplay(entries, lambda *a, **k: None, record=recorder)
        entries[0]["messages"][0]["content"] = "caller mutation"
        entries[0]["output"]["notes"].append("caller mutation")
        result = invoke(replay, original)
        self.assertEqual(result, original["output"])
        result["notes"].clear()
        self.assertEqual(replay.records[0]["output_hash"], digest(original["output"]))
        copied = replay.records; copied[0]["provenance"].clear()
        self.assertEqual(replay.records[0]["provenance"], original["provenance"])
        self.assertEqual(seen[0]["provenance"], original["provenance"])

    def test_parameter_object_key_order_is_irrelevant_but_types_are_preserved(self):
        replay = MaterialReplay([entry()], lambda *a, **k: None)
        current = entry()
        current["params"] = dict(reversed(list(current["params"].items())))
        self.assertEqual(invoke(replay, current), entry()["output"])
        changed = entry(); changed["params"]["max_tokens"] = 8000.0
        replay = MaterialReplay([entry()], lambda *a, **k: None)
        with self.assertRaises(ReplayMismatchError):
            invoke(replay, changed)

    def test_audit_failure_does_not_consume_prefix_or_start_live(self):
        calls = []
        def fail(event):
            raise OSError("audit unavailable")
        for entries in ([entry()], []):
            replay = MaterialReplay(entries, lambda *a, **k: calls.append(True), record=fail)
            with self.assertRaises(OSError):
                invoke(replay, entry())
            self.assertEqual(replay.summary()["replayed_calls"], 0)
            self.assertEqual(replay.summary()["real_calls"], 0)
            self.assertEqual(replay.summary()["consumed"], 0)
            self.assertTrue(replay.summary()["blocked"])
        self.assertEqual(calls, [])

    def test_live_failure_is_counted_and_not_retried(self):
        calls = []
        def fail(*a, **k):
            calls.append(True)
            raise RuntimeError("offline provider failure")
        replay = MaterialReplay([], fail)
        with self.assertRaises(RuntimeError):
            invoke(replay, entry())
        self.assertEqual(replay.summary()["real_calls"], 1)
        self.assertEqual(replay.records[-1]["event"], "live_failed")
        with self.assertRaises(ReplayMismatchError):
            invoke(replay, entry())
        self.assertEqual(len(calls), 1)

    def test_live_callback_receives_copies_and_output_is_not_semantically_repaired(self):
        original = entry()
        response = ["malformed material envelope is still actual provider output"]
        def live(step, messages, **params):
            messages.clear()
            params.clear()
            return response
        replay = MaterialReplay([], live)
        result = invoke(replay, original)
        self.assertEqual(result, response)
        result.append("mutated return")
        self.assertEqual(len(response), 1)
        self.assertEqual(original, entry())

    def test_missing_provenance_and_non_json_identity_are_rejected(self):
        for key in ("step", "messages", "params", "output", "provenance"):
            invalid = entry(); del invalid[key]
            with self.assertRaises(ValueError):
                MaterialReplay([invalid], lambda *a, **k: None)
        invalid = entry(); invalid["provenance"] = {}
        with self.assertRaises(ValueError):
            MaterialReplay([invalid], lambda *a, **k: None)
        invalid = entry(); invalid["params"]["temperature"] = float("nan")
        with self.assertRaises(ValueError):
            MaterialReplay([invalid], lambda *a, **k: None)
        calls = []
        replay = MaterialReplay([], lambda *a, **k: calls.append(True))
        with self.assertRaises(ValueError):
            replay("step", {}, max_tokens=1)
        self.assertEqual(calls, [])

    def test_actual_series_replays_exact_prefix_and_counts_only_suffix_as_real(self):
        specs = [{"session_id": i, "date": f"2025-01-0{i+1}", "intent": "natural period",
                  "document_count": 1, "target_chars": 20} for i in range(2)]
        outputs = [{"corpus": {"sessions": [{"session_id": i, "date": specs[i]["date"],
                    "docs": [{"title": "标题", "content": f"第{i}期记录，尚不能判断后续是否完成。"}]}]},
                    "public_protocol": "proposal only", "design_notes": {"next": "fallible hypothesis"},
                    "limitations": []} for i in range(2)]
        requests = []
        def source(step, messages, **params):
            index = len(requests)
            requests.append(dict(step=step, messages=deepcopy(messages), params=deepcopy(params),
                                 output=deepcopy(outputs[index]), provenance={"source_index": index}))
            return deepcopy(outputs[index])
        options = dict(model="offline", batch_specs=specs, public_protocol="fixed", max_calls=2,
                       max_input_chars=100000, max_tokens=1024)
        baseline = generate_material_series({}, "intent", chat_json=source, **options)
        live_calls = []
        def live(step, messages, **params):
            live_calls.append((step, messages, params))
            self.assertEqual(messages, requests[1]["messages"])
            return deepcopy(outputs[1])
        replay = MaterialReplay(requests[:1], live)
        resumed = generate_material_series({}, "intent", chat_json=replay, **options)
        self.assertEqual(resumed["corpus"], baseline["corpus"])
        self.assertEqual(resumed["calls_used"], 2)
        self.assertEqual(replay.summary()["replayed_calls"], 1)
        self.assertEqual(replay.summary()["real_calls"], 1)
        self.assertEqual(len(live_calls), 1)
        mismatched = MaterialReplay(requests[:1], live)
        failure = generate_material_series({}, "changed intent", chat_json=mismatched, **options)
        self.assertFalse(failure["downstream_ready"])
        self.assertEqual(failure["execution"]["reason"], "model_error")
        self.assertEqual(mismatched.summary()["real_calls"], 0)
        self.assertEqual(len(live_calls), 1)

    def test_import_has_no_provider_configuration_side_effect(self):
        code = '''import sys
class Deny:
    def find_spec(self, name, *args):
        if name == "config": raise AssertionError("forbidden config import")
sys.meta_path.insert(0, Deny())
from pipeline.material_replay import MaterialReplay
assert MaterialReplay([], lambda *a, **k: None).summary()["real_calls"] == 0
'''
        child = subprocess.run([sys.executable, "-X", "utf8=0", "-c", code], cwd=ROOT,
                               capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(child.returncode, 0, child.stderr)


if __name__ == "__main__":
    unittest.main()
