"""Offline SDK-level transport and prospective CLI integration, without dotenv I/O."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import builtins
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from llm_trace import trace_scope
from llm_transport_selftest import configuration
from llm_transport import transport_fingerprint, validate_transport
from llm_trace_selftest import response
from llm_offline_executor import install
from tools import run_bc_case, run_agent_case as cli
import agent_case_cli_selftest as cli_fixtures
from agent_factory_selftest import Script


class FakeSDK:
    def __init__(self, create=None):
        self.calls, self.options, self.lock = [], [], threading.Lock()
        self.create = create or (lambda **kw: response('{"ok":true}'))
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._legacy))

    def _call(self, options, kwargs):
        with self.lock:
            self.calls.append({"options": deepcopy(options), "kwargs": deepcopy(kwargs)})
        return self.create(**kwargs)

    def _legacy(self, **kwargs):
        return self._call(None, kwargs)

    def with_options(self, **options):
        with self.lock:
            self.options.append(deepcopy(options))
        return types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(
            create=lambda **kwargs: self._call(options, kwargs))))


def load_config(api):
    spec = importlib.util.spec_from_file_location("offline_profile_config", ROOT / "config.py")
    value = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"dotenv": types.SimpleNamespace(load_dotenv=lambda *a: None),
            "openai": types.SimpleNamespace(OpenAI=lambda **kw: api)}), patch.dict(os.environ, {
            "OPENAI_API_KEY": "offline-secret-token", "OPENAI_BASE_URL": "https://offline.invalid/v1",
            "MODEL": "offline-model", "LLM_MIN_COMPLETION_TOKENS": "0", "LLM_HTTP_READ_TIMEOUT_S": "580",
            "LLM_DEADLINE_S": "600", "LLM_CONCURRENCY": "8"}):
        spec.loader.exec_module(value)
    install(value, api)
    return value


class ConfigTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / "attempts.jsonl"
        self.api = FakeSDK(); self.config = load_config(self.api)

    def rows(self):
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    def test_explicit_profile_wire_trace_and_exact_stage_budget(self):
        profile = configuration()
        self.config.MIN_COMPLETION_TOKENS = 99999
        with trace_scope(self.path, "test.profile"):
            result = self.config.chat_json([], model="explicit-reasoner", transport=profile,
                max_tokens=16384, retries=1, strict_json=True)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(self.api.calls[0]["kwargs"], {"messages": [], "model": "explicit-reasoner",
            "reasoning_effort": "high", "max_completion_tokens": 16384, "response_format": {"type": "json_object"}})
        self.assertEqual(self.api.calls[0]["options"], {"timeout": 300, "max_retries": 0})
        request = next(row for row in self.rows() if row["event"] == "request")
        self.assertEqual(request["logical_parameters"]["max_tokens"], 16384)
        self.assertEqual(request["logical_parameters"]["temperature"], 0.7)
        self.assertNotIn("temperature", request["parameters"])
        self.assertEqual(request["transport"]["fingerprint"], transport_fingerprint(profile))
        self.assertEqual(request["sdk_options"], {"timeout_seconds": 300, "max_retries": 0})

    def test_legacy_does_not_import_profile_or_clone_client(self):
        original_import = builtins.__import__
        def checked(name, *args, **kwargs):
            if name == "llm_transport":
                raise AssertionError("legacy imported profile dependency")
            return original_import(name, *args, **kwargs)
        self.config.MIN_COMPLETION_TOKENS = 9000
        with patch("builtins.__import__", side_effect=checked), trace_scope(self.path, "legacy"):
            self.config.chat_json([], max_tokens=32, retries=1, strict_json=True)
        self.assertEqual(self.api.options, [])
        self.assertEqual(self.api.calls[0]["kwargs"], {"messages": [], "model": "offline-model",
                         "temperature": 0.7, "top_p": 1.0, "max_tokens": 9000})

    def test_different_concurrent_profiles_never_mutate_shared_configuration(self):
        barrier = threading.Barrier(2)
        self.api.create = lambda **kwargs: (barrier.wait(timeout=5), response('{"ok":true}'))[1]
        first, second = configuration(), configuration()
        second["profiles"]["high_json"].update(reasoning_effort="medium", http_timeout_seconds=60, deadline_seconds=61)
        before = deepcopy((first, second)); environment = dict(os.environ)
        def invoke(pair):
            value, tokens = pair
            with trace_scope(self.path, "parallel"):
                return self.config.chat_json([], model="explicit-reasoner", transport=value,
                    max_tokens=tokens, retries=1, strict_json=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(invoke, [(first, 40), (second, 50)])), [{"ok": True}] * 2)
        calls = {call["kwargs"]["max_completion_tokens"]: call for call in self.api.calls}
        self.assertEqual((calls[40]["kwargs"]["reasoning_effort"], calls[40]["options"]["timeout"]), ("high", 300))
        self.assertEqual((calls[50]["kwargs"]["reasoning_effort"], calls[50]["options"]["timeout"]), ("medium", 60))
        self.assertEqual((first, second), before)
        self.assertEqual(dict(os.environ), environment)
        self.assertEqual(len(self.api.calls), 2)
        self.assertEqual(self.config.DEADLINE_S, 600)

    def test_invalid_or_conflicting_profile_fails_before_attempt(self):
        invalid = configuration(); invalid["profiles"]["high_json"]["messages"] = []
        for profile, format_value in ((invalid, None), (configuration(), {"type": "text"})):
            with self.subTest(profile=profile), trace_scope(self.path, "bad.configuration"), self.assertRaises(ValueError):
                self.config.chat_json([], model="explicit-reasoner", transport=profile,
                    response_format=format_value, retries=3, strict_json=True)
        self.assertEqual(self.api.calls, [])
        self.assertEqual(self.api.options, [])
        self.assertFalse(self.path.exists())

    def test_api_and_parse_failures_each_have_one_attempt_and_raw_survives(self):
        bad = '{"broken":' + "x" * 1600
        for value in (RuntimeError("offline API error"), bad):
            self.api.calls.clear(); self.api.options.clear()
            def create(**kw):
                if isinstance(value, Exception):
                    raise value
                return response(value)
            self.api.create = create
            with trace_scope(self.path, "failure"), self.assertRaises(ValueError):
                self.config.chat_json([], model="explicit-reasoner", transport=configuration(), retries=1, strict_json=True)
            self.assertEqual(len(self.api.calls), 1)
            self.assertEqual(len(self.api.options), 1)
        self.assertEqual(sum(row["event"] == "request" for row in self.rows()), 2)
        self.assertEqual([row for row in self.rows() if row["event"] == "json_error"][-1]["raw_output"], bad)

    def test_deadline_comes_from_profile(self):
        deadlines = []
        original = self.config._perform_request
        def observed(**kwargs):
            deadlines.append(kwargs["deadline_s"])
            return original(**kwargs)
        profile = configuration(); profile["profiles"]["high_json"]["deadline_seconds"] = 0.125
        with patch.object(self.config, "_perform_request", observed), trace_scope(self.path, "deadline"):
            self.config.chat([], model="explicit-reasoner", transport=profile)
        self.assertEqual(len(deadlines), 1)
        self.assertGreater(deadlines[0], 0)
        self.assertLessEqual(deadlines[0], 0.125)

    def test_provider_validates_before_config_and_freezes_its_copy(self):
        original_import = builtins.__import__
        def checked(name, *args, **kwargs):
            if name == "config":
                raise AssertionError("invalid profile reached config import")
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=checked), self.assertRaises(ValueError):
            run_bc_case.provider(self.directory, transport={})
        profile = configuration()
        with patch.dict(sys.modules, {"config": self.config}):
            call, _, _ = run_bc_case.provider(self.directory, transport=profile)
            profile["profiles"]["high_json"]["reasoning_effort"] = "mutated"
            call("provider", [], model="explicit-reasoner", retries=1, strict_json=True)
        self.assertEqual(self.api.calls[0]["kwargs"]["reasoning_effort"], "high")
        with self.assertRaises(ValueError):
            call("provider", [], model="explicit-reasoner", retries=1, transport=configuration())
        self.assertEqual(len(self.api.calls), 1)

    def test_legacy_provider_passes_no_extra_transport_key(self):
        observed = []
        fake_config = types.SimpleNamespace(_trace_secrets=lambda: (), chat_json=lambda messages, **params: observed.append(params))
        with patch.dict(sys.modules, {"config": fake_config}):
            call, _, _ = run_bc_case.provider(self.directory)
            call("legacy", [], retries=1)
        self.assertEqual(observed, [{"retries": 1}])


class CliTransportTests(unittest.TestCase):
    setUp = cli_fixtures.AgentCaseCliTests.setUp
    write_plan = cli_fixtures.AgentCaseCliTests.write_plan

    def configure_profile(self):
        profile = configuration(); profile["default_profile"] = "high_json"
        self.plan["transport"] = profile
        self.plan["source_sha256"]["llm_transport.py"] = cli._hash((ROOT / "llm_transport.py").read_bytes())
        self.write_plan()

    def test_conditional_source_inventory_and_no_legacy_import(self):
        self.assertNotIn("llm_transport.py", cli.source_names(self.plan["factory_plan"]))
        self.assertIn("llm_transport.py", cli.source_names(self.plan["factory_plan"], transport=configuration()))
        code = """import builtins, sys
original = builtins.__import__
def checked(name, *a, **kw):
    if name.split('.')[0] in {'config','openai','dotenv','llm_transport'}:
        raise AssertionError('Unexpected import: ' + name)
    return original(name, *a, **kw)
builtins.__import__ = checked
from tools.run_agent_case import run
assert run(sys.argv[1])['calls_permitted'] == 0
"""
        result = subprocess.run([sys.executable, "-c", code, str(self.case)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_profile_source_hash_checked_before_profile_module_import(self):
        self.configure_profile(); self.plan["source_sha256"]["llm_transport.py"] = "0" * 64; self.write_plan()
        original_import = builtins.__import__
        def checked(name, *a, **kw):
            if name in {"config", "llm_transport"}:
                raise AssertionError("import happened before source check")
            return original_import(name, *a, **kw)
        with patch("builtins.__import__", side_effect=checked), self.assertRaises(ValueError):
            cli.run(self.case, execute=True)
        self.assertFalse((self.case / "execution").exists())

    def test_profile_dry_run_and_invalid_configuration_never_initialize_provider(self):
        self.configure_profile()
        with patch.object(cli, "provider", side_effect=AssertionError("No provider in dry run")):
            result = cli.run(self.case)
        self.assertEqual(result["execution_transport"]["configuration"], validate_transport(self.plan["transport"]))
        self.plan["transport"]["profiles"]["high_json"]["retries"] = 3; self.write_plan()
        with patch.object(cli, "provider", side_effect=AssertionError("No provider for invalid plan")), self.assertRaises(ValueError):
            cli.run(self.case, execute=True)
        self.assertFalse((self.case / "execution").exists())

    def test_real_cli_factory_provider_config_sdk_chain_preserves_budget_and_bindings(self):
        self.configure_profile()
        script = Script()
        def create(**kwargs):
            model = kwargs["model"]
            step = "semantic_review.blind_read" if model.endswith(".reader") else "semantic_review.adjudicate"
            value = script(step, kwargs["messages"], model=model)
            return response(json.dumps(value, ensure_ascii=False))
        api = FakeSDK(create); config = load_config(api)
        config.MIN_COMPLETION_TOKENS = 99999
        with patch.dict(sys.modules, {"config": config}), patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
            result = cli.run(self.case, execute=True)
        directory = self.case / "execution"
        self.assertEqual(result["actual_provider_attempts"], 14)
        self.assertEqual(len(api.calls), 14)
        self.assertEqual(len(api.options), 14)
        self.assertTrue(all(call["kwargs"]["max_completion_tokens"] == 4096 for call in api.calls))
        self.assertTrue(all("temperature" not in call["kwargs"] and "max_tokens" not in call["kwargs"] for call in api.calls))
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["execution_transport"], report["execution_transport"])
        self.assertEqual(report["execution_transport"]["fingerprint"], transport_fingerprint(self.plan["transport"]))
        self.assertTrue((directory / "source_snapshot/llm_transport.py").exists())
        from pipeline.quality_workflow import validate_review
        validate_review(report["current_snapshot"], report["phases"]["final_review"])
        checkpoint = json.loads((directory / "phases/final_review/case_checkpoint.json").read_text(encoding="utf-8"))
        self.assertEqual(checkpoint["execution_transport"], report["execution_transport"])
        attempts = [json.loads(line) for line in (directory / "attempts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sum(row["event"] == "request" for row in attempts), 14)
        self.assertTrue(all("transport" not in call["payload"] for call in script.calls))

    def test_profile_zero_global_budget_makes_no_sdk_request(self):
        self.configure_profile(); self.plan["factory_plan"]["max_provider_attempts"] = 0; self.write_plan()
        api = FakeSDK(); config = load_config(api)
        with patch.dict(sys.modules, {"config": config}):
            result = cli.run(self.case, execute=True)
        self.assertEqual(api.calls, [])
        self.assertEqual(result["actual_provider_attempts"], 0)


if __name__ == "__main__":
    unittest.main()
