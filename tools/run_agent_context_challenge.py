"""Optional, bounded context trials on a completed agent case.

Preparation and the default run are offline. Execution never rewrites the
generation receipt, formal grades, or question selection. Trials and collection
interpretation are observations, not a memory score or an extra release gate.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_bc_case import read, save, sha, provider
from tools.agent_pipeline import _upstream, _path, _json, _write_bytes
from tools.run_agent_evaluation import SOURCES as EVALUATION_SOURCES, runtime_identity, dispatch_ledger
from llm_transport import validate_transport, resolve_profile

VERSION = "agent-context-challenge-case/v1"
SPEC_VERSION = "agent-context-challenge-spec/v1"
SETTINGS = {"trials", "solver", "interpreter", "transport", "max_provider_attempts",
            "max_input_chars", "full_context_char_budget"}
SOURCES = tuple(sorted(set(EVALUATION_SOURCES) | {
    "tools/run_agent_context_challenge.py", "tools/agent_pipeline.py",
    "pipeline/context_challenge.py", "pipeline/question_set_review.py"}))


def _hash(blob):
    return hashlib.sha256(blob).hexdigest()


def source_hashes():
    return {name: sha(ROOT / name) for name in SOURCES}


def _positive(value, label):
    if type(value) is not int or value < 1:
        raise ValueError(label + " must be a positive integer")


def _model(value, label):
    if (not isinstance(value, dict) or set(value) != {"model", "max_tokens"}
            or not isinstance(value["model"], str) or not value["model"].strip()):
        raise ValueError(label + " requires model and max_tokens")
    _positive(value["max_tokens"], label + ".max_tokens")


def _settings(plan):
    from pipeline.context_challenge import prepare_challenge
    value = plan["settings"]
    if not isinstance(value, dict) or set(value) != SETTINGS:
        raise ValueError("Unexpected context challenge settings")
    _model(value["solver"], "solver")
    if value["interpreter"] is not None:
        _model(value["interpreter"], "interpreter")
    for field in ("max_input_chars", "full_context_char_budget", "max_provider_attempts"):
        _positive(value[field], field)
    validate_transport(value["transport"])
    profile = resolve_profile(value["transport"], value["solver"]["model"])
    for role in (value["solver"], value["interpreter"]):
        if role is None:
            continue
        active = resolve_profile(value["transport"], role["model"])["profile"]
        if (not isinstance(active, dict) or "http_timeout_seconds" not in active
                or "deadline_seconds" not in active
                or active.get("response_format") != {"type": "json_object"}):
            raise ValueError("Every role requires an explicit JSON profile with HTTP timeout and deadline")
    if not isinstance(value["trials"], list) or not value["trials"]:
        raise ValueError("At least one predeclared trial is required")
    expected = len(value["trials"]) + int(value["interpreter"] is not None)
    if value["max_provider_attempts"] != expected:
        raise ValueError("Explicit call cap must equal one attempt per trial plus optional interpretation")
    options = {**value["solver"], "max_input_chars": value["max_input_chars"],
               "full_context_char_budget": value["full_context_char_budget"],
               "solver_identity": {"transport": deepcopy(value["transport"]), "profile": profile}}
    return value, options, prepare_challenge


def prepare(spec, case):
    spec = Path(spec).resolve(strict=True)
    blob = spec.read_bytes()
    value = _json(blob)
    if not isinstance(value, dict) or set(value) != {"version", "source_case", *SETTINGS} or value["version"] != SPEC_VERSION:
        raise ValueError("Explicit context challenge v1 spec required")
    case = Path(case).resolve()
    if case.exists():
        raise FileExistsError("Case already exists; choose a new directory")
    source_case = _path(value["source_case"], spec.parent, directory=True)
    source, lineage = _upstream(source_case)
    snapshot = _json(source["snapshot"])
    settings = {key: deepcopy(value[key]) for key in SETTINGS}
    plan = {"version": VERSION, "result_scope": "research_only", "settings": settings,
            "lineage": lineage, "runtime": runtime_identity(), "source_sha256": source_hashes(),
            "spec_sha256": _hash(blob),
            "input_sha256": {name + ".json": _hash(data) for name, data in source.items()}}
    _, options, prepare_function = _settings(plan)
    prepared = prepare_function(snapshot, settings["trials"], **options)
    # All addressing and budgets are checked before creating a case. There is
    # no provider configuration or network request in this preparation path.
    case.mkdir(parents=True, exist_ok=False)
    _write_bytes(case / "spec.original.json", blob)
    for name, raw in source.items():
        _write_bytes(case / "inputs" / (name + ".json"), raw)
    save(case / "prepared_trials.json", prepared)
    plan["prepared_trials_sha256"] = sha(case / "prepared_trials.json")
    save(case / "plan.json", plan)
    result = run(case, execute=False)
    save(case / "preparation.json", {**result, "version": VERSION,
         "plan_sha256": sha(case / "plan.json"), "spec_sha256": _hash(blob)})
    return result


def _prepare(case):
    plan = read(case / "plan.json")
    if plan.get("version") != VERSION or plan.get("result_scope") != "research_only":
        raise ValueError("Unrecognized context challenge plan")
    if plan.get("runtime") != runtime_identity() or plan.get("source_sha256") != source_hashes():
        raise ValueError("Runtime/source changed; prepare an explicit new case")
    if sha(case / "spec.original.json") != plan.get("spec_sha256"):
        raise ValueError("Frozen spec changed")
    original_spec = _json((case / "spec.original.json").read_bytes())
    if ({key: original_spec.get(key) for key in SETTINGS} != plan.get("settings")
            or original_spec.get("version") != SPEC_VERSION):
        raise ValueError("Plan settings differ from the reviewed spec")
    marker_path = case / "preparation.json"
    if marker_path.exists():
        marker = read(marker_path)
        if (marker.get("version") != VERSION or marker.get("ready") is not True
                or marker.get("plan_sha256") != sha(case / "plan.json")
                or marker.get("spec_sha256") != plan["spec_sha256"]):
            raise ValueError("Preparation marker no longer binds this plan")
    value, options, prepare_function = _settings(plan)
    expected_inputs = {name + ".json" for name in ("report", "original_snapshot", "snapshot", "receipt", "review")}
    if set(plan.get("input_sha256", {})) != expected_inputs:
        raise ValueError("Incomplete upstream artifact freeze")
    for name, expected in plan["input_sha256"].items():
        if sha(case / "inputs" / name) != expected:
            raise ValueError("Frozen upstream input changed: " + name)
    for entry in plan["lineage"]["files"].values():
        if sha(entry["path"]) != entry["sha256"]:
            raise ValueError("Original upstream artifact changed")
    if ({name + ".json": entry["sha256"] for name, entry in plan["lineage"]["files"].items()}
            != plan["input_sha256"]):
        raise ValueError("Prepared copies differ from the actual upstream lineage")
    snapshot = read(case / "inputs/snapshot.json")
    prepared = prepare_function(snapshot, value["trials"], **options)
    if (sha(case / "prepared_trials.json") != plan["prepared_trials_sha256"]
            or prepared != read(case / "prepared_trials.json")):
        raise ValueError("Prepared trial messages or identities changed")
    return plan, value, options, snapshot, prepared


def _parse_response(text):
    # Match the existing transport's JSON envelope allowance; no rewriting of
    # answer contents. The parsed output is also compared to the saved raw.
    clean = re.sub(r"^```(?:json)?\s*", "", text.strip())
    clean = re.sub(r"\s*```$", "", clean)
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\})", clean, re.DOTALL)
        if match is None:
            raise
        return json.loads(match.group(1))


def _ledger(directory, settings, reservations):
    ledger = dispatch_ledger(directory, {"transport": settings["transport"]}, reservations)
    path = directory / "attempts.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    requests = [event for event in events if event.get("event") == "request"]
    responses = {event["call_id"]: event for event in events if event.get("event") == "response"}
    checks = []
    for request, slot in zip(requests, reservations):
        saved = directory / "returns" / f"{slot['attempt']:03d}.json"
        response = responses.get(request.get("call_id"))
        try:
            raw = _parse_response(response["response"]["choices"][0]["content"])
            match = saved.exists() and raw == read(saved)
        except (KeyError, IndexError, TypeError, ValueError):
            match = False
        checks.append(request.get("messages") == slot["messages"] and match)
    ledger["exact_messages_and_raw_match"] = len(requests) == len(reservations) and all(checks)
    return ledger


def run(case, *, execute=False):
    from pipeline.context_challenge import run_challenge, prepare_collection_evidence
    from pipeline.question_set_review import review_question_set
    case = Path(case).resolve(strict=True)
    directory = case / "execution"
    if directory.exists():
        raise FileExistsError("Execution already claimed; no automatic restart")
    plan, settings, options, snapshot, prepared = _prepare(case)
    if not execute:
        return {"case": str(case), "ready": True, "execute": False, "calls_permitted": 0,
                "planned_max_provider_attempts": settings["max_provider_attempts"],
                "snapshot_id": snapshot["snapshot_id"], "trial_count": len(settings["trials"]),
                "formal_scoring": False, "publication_effect": "none"}
    if not (case / "preparation.json").exists():
        raise ValueError("Run prepare successfully before executing a context challenge")
    plan_hash = sha(case / "plan.json")
    directory.mkdir(exist_ok=False)
    mapping = {name: f"{index:03d}_{Path(name).name}" for index, name in enumerate(SOURCES)}
    manifest = {"version": VERSION, "plan": plan, "plan_sha256": plan_hash,
        "started_unix": time.time(), "source_copy_paths": mapping, "official_release": False,
        "automatic_retry": False, "overwrite_or_restart": False}
    try:
        for name in plan["input_sha256"]:
            _write_bytes(directory / "inputs" / name, (case / "inputs" / name).read_bytes())
        for name, destination in mapping.items():
            _write_bytes(directory / "source_snapshot" / destination, (ROOT / name).read_bytes())
        save(directory / "manifest.json", manifest)
    except Exception as exc:
        save(directory / "report.json", {"version": VERSION, "result_scope": "research_only",
            "execution": {"status": "failed", "stage": "claim_artifacts"},
            "failures": [{"error_type": type(exc).__name__, "message": str(exc)}],
            "actual_provider_attempts": 0, "formal_scoring": False, "generation_receipt_modified": False})
        return {"case": str(case), "execution": {"status": "failed"}, "actual_provider_attempts": 0}
    reservations, failures = [], []
    clean = deepcopy
    challenge = interpretation = None
    report = {"version": VERSION, "snapshot_id": snapshot["snapshot_id"], "result_scope": "research_only",
              "publication_effect": "none", "formal_scoring": False, "generation_receipt_modified": False,
              "baseline_comparison": "No matched full baseline imported; no paired performance claim.",
              "execution": {"status": "running"}}
    try:
        call, record, clean = provider(directory, transport=settings["transport"])

        def validate_execution():
            if read(directory / "manifest.json") != manifest:
                raise ValueError("Execution manifest changed")
            if any(sha(directory / "source_snapshot" / mapping[name]) != expected
                   for name, expected in plan["source_sha256"].items()):
                raise ValueError("Execution source copy changed")
            if any(sha(directory / "inputs" / name) != expected
                   for name, expected in plan["input_sha256"].items()):
                raise ValueError("Execution input copy changed")

        def dispatch(step, messages, **params):
            if failures:
                raise RuntimeError("Earlier dispatch/audit failure; no further calls")
            try:
                if sha(case / "plan.json") != plan_hash:
                    raise ValueError("Frozen plan changed")
                _prepare(case)
                validate_execution()
                expected_model = settings["solver"] if step == "context_challenge.answer" else (
                    settings["interpreter"] if step == "agent_editing.question_set_review" else None)
                expected = {**(expected_model or {}), "temperature": 0.0, "retries": 1, "strict_json": True}
                if step == "context_challenge.answer":
                    expected["top_p"] = 1.0
                if expected_model is None or params != expected:
                    raise ValueError("Undeclared role or request parameters")
                if len(reservations) >= settings["max_provider_attempts"]:
                    raise RuntimeError("Provider attempt cap exhausted")
                if sum(len(m["content"]) for m in messages) > settings["max_input_chars"]:
                    raise ValueError("Actual messages exceed input budget")
                entry = {"attempt": len(reservations) + 1, "step": step,
                         "messages": deepcopy(messages), "params": deepcopy(params)}
                save(directory / "reservations" / f"{entry['attempt']:03d}.json", clean(entry))
                reservations.append(entry)
                raw = call(step, deepcopy(messages), **params)
                save(directory / "returns" / f"{entry['attempt']:03d}.json", clean(raw))
                # Audit each physical return before allowing another role to
                # dispatch; a final-only check would allow needless later calls.
                observed = _ledger(directory, settings, reservations)
                if not all(observed[key] for key in ("all_observed_wires_match", "complete_response_audit", "exact_messages_and_raw_match")):
                    raise RuntimeError("Provider wire/raw audit failed; subsequent calls stopped")
                _prepare(case)
                validate_execution()
                return raw
            except Exception as exc:
                failures.append({"step": step, "error_type": type(exc).__name__, "message": str(exc)})
                raise

        completed = []

        def checkpoint(trial):
            save(directory / "trials" / f"{len(completed) + 1:03d}.json", clean(trial))
            completed.append(trial["id"])
            print(json.dumps({"trial": trial["id"], "execution": trial["execution"]}, ensure_ascii=False), flush=True)

        challenge = run_challenge(snapshot, settings["trials"], **options,
            chat_json=dispatch, max_calls=len(settings["trials"]), record=record, on_trial=checkpoint)
        save(directory / "challenge.json", clean(challenge))
        if challenge["execution"]["status"] != "completed" or failures:
            raise RuntimeError("Context trials incomplete; interpretation not dispatched")
        evidence = prepare_collection_evidence(snapshot, challenge)
        save(directory / "collection_evidence.json", clean(evidence))
        if settings["interpreter"] is not None:
            interpretation = review_question_set(snapshot["questions"], snapshot["corpus"], snapshot["protocol"],
                **settings["interpreter"], chat_json=dispatch, max_calls=1,
                max_input_chars=settings["max_input_chars"], record=record,
                context_challenge_evidence=evidence)
            save(directory / "interpretation.json", clean(interpretation))
            if interpretation["execution"]["status"] != "ok" or failures:
                raise RuntimeError("Collection interpretation did not complete")
        _prepare(case)
        validate_execution()
        if sha(case / "plan.json") != plan_hash:
            raise ValueError("Frozen plan changed after execution")
        ledger = _ledger(directory, settings, reservations)
        if not all(ledger[key] for key in ("all_observed_wires_match", "complete_response_audit", "exact_messages_and_raw_match")):
            raise RuntimeError("Provider ledger does not match reservations and returns")
        report.update(execution={"status": "completed"}, ledger=ledger)
    except Exception as exc:
        failures.append({"stage": "runner", "error_type": type(exc).__name__, "message": str(exc)})
        report["execution"] = {"status": "failed"}
        partial = getattr(exc, "report", None)
        if partial is not None:
            save(directory / "failed_stage_report.json", clean(partial))
        try:
            report["ledger"] = _ledger(directory, settings, reservations)
        except Exception as audit_error:
            report["ledger_error"] = {"error_type": type(audit_error).__name__, "message": str(audit_error)}
    report.update(failures=failures, actual_provider_attempts=len(reservations),
        max_provider_attempts=settings["max_provider_attempts"], challenge_completed=(challenge or {}).get("execution", {}).get("status") == "completed",
        interpretation_completed=(interpretation or {}).get("execution", {}).get("status") == "ok")
    save(directory / "report.json", clean(report))
    return {"case": str(case), "execution": report["execution"],
            "actual_provider_attempts": len(reservations), "max_provider_attempts": settings["max_provider_attempts"],
            "result_scope": "research_only", "formal_scoring": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("prepare")
    command.add_argument("--spec", type=Path, required=True)
    command.add_argument("--case", type=Path, required=True)
    command = commands.add_parser("run")
    command.add_argument("--case", type=Path, required=True)
    command.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = prepare(args.spec, args.case) if args.command == "prepare" else run(args.case, execute=args.execute)
        print(json.dumps(result, ensure_ascii=False))
        return int(result.get("execution", {}).get("status") == "failed")
    except Exception as exc:
        print(json.dumps({"ready": False, "error_type": type(exc).__name__, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
