"""Start a fresh question cycle from unchanged originals of a failed case.

The new case contains plan.json, snapshot.json and exact copies under upstream/
of the declared upstream kind, its old inputs and source snapshots. The plan declares cycle_plan, transport, input_sha256,
source_sha256 and upstream_execution_directory. Dry run is the default; execute
claims a new execution directory and never retries, resumes it or generates new
questions. The old failed run remains a separate, hash-bound lineage.
An explicit cycle_plan review_reuse='same-input-within-run/v1' may retain this
new invocation's completed initial opinions after a whole-batch no-change edit;
it never imports the upstream run's opinions. The default remains fresh.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
from threading import RLock
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.agent_factory import (QUESTION_CYCLE_PHASES, prepare_question_cycle,
    run_agent_question_cycle)
from tools.run_agent_case import (INPUTS as CASE_INPUTS, _exports, _write_bytes,
    source_names as case_source_names)
from tools.run_bc_case import provider, save

PLAN_VERSION = "agent-question-cycle-plan/v1"
UPSTREAM_FILES = ("failed_report.json", "failure.json", "manifest.json", "attempt_count.json",
                  "phases/question_generation/report.json", "attempts.jsonl", "calls.jsonl")
INPUTS = ("plan.json", "snapshot.json") + tuple("upstream/" + name for name in UPSTREAM_FILES)
UPSTREAM_KINDS = {"initial_review_failure": UPSTREAM_FILES,
    "completed_unedited_execution_gaps": ("report.json", "original_snapshot.json", "snapshot.json",
        "receipt.json", "manifest.json", "attempt_count.json", "phases/question_generation/report.json",
        "attempts.jsonl", "calls.jsonl")}


def _hash(blob):
    return hashlib.sha256(blob).hexdigest()


def source_names(cycle_plan):
    # agent_factory imports generation modules even though this entry never calls
    # them. Freeze those imports, package initializers and the shared exporters.
    names = case_source_names({"material_mode": "team/v1", "phases": cycle_plan["phases"]}, transport={})
    return tuple(dict.fromkeys((*names, "tools/run_agent_question_cycle.py", "pipeline/__init__.py",
        "eval/__init__.py", "tools/__init__.py")))


def _json(blob):
    return json.loads(blob.decode("utf-8-sig"))


def upstream_files(kind, manifest):
    """Exact source evidence to copy; paths cannot escape the old execution."""
    if kind not in UPSTREAM_KINDS:
        raise ValueError("Unknown explicit upstream kind")
    inputs, sources = manifest.get("input_sha256", {}), manifest.get("source_sha256", {})
    if set(inputs) != set(CASE_INPUTS) or not isinstance(sources, dict) or not sources:
        raise ValueError("Upstream manifest must retain its complete input and source freeze")
    for name in (*inputs, *sources):
        if not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Upstream evidence paths must be relative and contained")
    return (*UPSTREAM_KINDS[kind], *("inputs/" + name for name in inputs),
            *("source_snapshot/" + name for name in sources))


def _upstream_ledger(blobs, report):
    """Reconcile reservations with wire evidence without assuming worker order."""
    rows = [_json(line) for line in blobs["attempts.jsonl"].splitlines() if line.strip()]
    requests = [r for r in rows if r.get("event") == "request"]
    terminals = [r for r in rows if r.get("event") in {"response", "call_error"}]
    ids = [r.get("call_id") for r in requests]
    if (any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids)
            or Counter(r.get("call_id") for r in terminals) != Counter(ids)):
        raise ValueError("Upstream request IDs or terminal wire responses do not reconcile")
    operations = [r.get("operation_id") for r in requests]
    logical = [r for r in rows if r.get("event") == "json_attempt"]
    json_ends = [r for r in rows if r.get("event") in {"json_result", "json_error"}]
    if (any(not isinstance(i, str) or not i for i in operations) or len(set(operations)) != len(operations)
            or Counter(r.get("operation_id") for r in logical) != Counter(operations)
            or Counter(r.get("operation_id") for r in json_ends) != Counter(operations)
            or any(r.get("attempt") != 1 or r.get("max_attempts") != 1 for r in logical)
            or len({r.get("attempt_id") for r in logical}) != len(logical)
            or any(not isinstance(r.get("attempt_id"), str) or not r["attempt_id"] for r in logical)
            or any(r.get("sdk_options", {}).get("max_retries") != 0 for r in requests)):
        raise ValueError("Upstream logical attempts, retries or JSON terminal records do not reconcile")
    by_operation = {r["operation_id"]: r for r in json_ends}
    by_call = {r["call_id"]: r for r in terminals}
    by_logical = {r["operation_id"]: r for r in logical}
    def signature(step, model, messages, status, raw):
        return json.dumps([step, model, messages, status, raw], ensure_ascii=False, sort_keys=True, allow_nan=False)
    wire = []
    for request in requests:
        end, terminal = by_operation[request["operation_id"]], by_call[request["call_id"]]
        logical_start = by_logical[request["operation_id"]]
        if (terminal.get("operation_id") != request["operation_id"]
                or any(row.get("step") != request.get("step") for row in (end, terminal, logical_start))
                or end.get("attempt_id") != logical_start["attempt_id"]
                or (end["event"] == "json_result" and terminal["event"] != "response")):
            raise ValueError("Upstream wire operation identities differ")
        wire.append(signature(request.get("step"), request.get("model"), request.get("messages"),
            "returned" if end["event"] == "json_result" else "provider_error", end.get("parsed")))
    reserved = [signature(a.get("step"), a.get("params", {}).get("model"), a.get("messages"),
        a.get("status"), a.get("raw_output")) for a in report["provider_attempts"]]
    if Counter(wire) != Counter(reserved) or len(requests) != report["budget"]["actual_provider_attempts"]:
        raise ValueError("Upstream provider reservations differ from actual wire requests or raw returns")
    calls = [_json(line) for line in blobs["calls.jsonl"].splitlines() if line.strip()]
    if calls != report.get("records"):
        raise ValueError("Upstream recorded calls differ from the saved report")
    return {"requests": len(requests), "responses": sum(r["event"] == "response" for r in terminals),
        "provider_errors": sum(r["event"] == "call_error" for r in terminals),
        "json_results": sum(r["event"] == "json_result" for r in json_ends),
        "json_errors": sum(r["event"] == "json_error" for r in json_ends)}


def _prepare(case):
    plan_blob = (case / "plan.json").read_bytes()
    plan = _json(plan_blob)
    if plan.get("version") != PLAN_VERSION or plan.get("result_scope") != "research_only":
        raise ValueError("An explicit research-only question-cycle plan is required")
    kind = plan.get("upstream_kind", "initial_review_failure")
    manifest = _json((case / "upstream/manifest.json").read_bytes())
    selected_upstream = upstream_files(kind, manifest)
    inputs = ("snapshot.json", *("upstream/" + name for name in selected_upstream))
    blobs = {"plan.json": plan_blob, **{name: (case / name).read_bytes() for name in inputs}}
    expected = plan.get("input_sha256", {})
    if set(expected) != set(inputs) or any(_hash(blobs[k]) != h for k, h in expected.items()):
        raise ValueError("Plan must freeze every original snapshot and upstream input byte")
    original_path = Path(plan["upstream_execution_directory"])
    if not original_path.is_absolute():
        raise ValueError("An absolute upstream execution directory is required")
    upstream_dir = original_path.resolve(strict=True)
    if case == upstream_dir or case in upstream_dir.parents or upstream_dir in case.parents:
        raise ValueError("Continuation must use a separate new case directory")
    upstream_blobs = {name: (upstream_dir / name).read_bytes() for name in selected_upstream}
    if any(blobs["upstream/" + name] != blob for name, blob in upstream_blobs.items()):
        raise ValueError("Upstream copies must retain exact original failed-run bytes")
    report_name = UPSTREAM_KINDS[kind][0]
    failed = _json(upstream_blobs[report_name])
    count, generated = (_json(upstream_blobs[name]) for name in
                        ("attempt_count.json", "phases/question_generation/report.json"))
    if kind == "initial_review_failure":
        failure = _json(upstream_blobs["failure.json"])
        if failure.get("status") != "failed_execution_preserved" or failure.get("error_type") != "AgentCaseError":
            raise ValueError("Initial failure kind requires the actual saved AgentCaseError")
    elif ((upstream_dir / "failure.json").exists() or (upstream_dir / "failed_report.json").exists()
            or any(_json(upstream_blobs[name]) != failed.get(key) for name, key in
                   (("original_snapshot.json", "original_snapshot"), ("snapshot.json", "current_snapshot"),
                    ("receipt.json", "receipt")))):
        raise ValueError("Completed-gap kind requires the actual completed report, snapshots and receipt")
    if (manifest.get("result_scope") != "research_only"
            or (Path(manifest["case"]).resolve() / "execution") != upstream_dir
            or count != failed.get("budget")
            or generated != failed.get("phases", {}).get("question_generation")):
        raise ValueError("Upstream manifest, failure, counts or generation lineage differs")
    old_plan = _json(upstream_blobs["inputs/plan.json"])
    old_sources = manifest["source_sha256"]
    if (old_plan.get("factory_plan") != failed.get("plan")
            or old_plan.get("source_sha256") != old_sources
            or set(old_plan.get("input_sha256", {})) != set(CASE_INPUTS) - {"plan.json"}
            or any(old_plan["input_sha256"][name] != manifest["input_sha256"][name]
                   for name in old_plan["input_sha256"])
            or any(_hash(upstream_blobs["inputs/" + name]) != value for name, value in manifest["input_sha256"].items())
            or any(_hash(upstream_blobs["source_snapshot/" + name]) != value for name, value in old_sources.items())):
        raise ValueError("Upstream old inputs or source snapshots differ from their original plan/manifest")
    upstream_case_blobs = {name: (upstream_dir.parent / name).read_bytes() for name in CASE_INPUTS}
    if (any(blob != upstream_blobs["inputs/" + name] for name, blob in upstream_case_blobs.items())
            or failed.get("inputs") != {"seed": _json(upstream_case_blobs["seed.json"]),
                "design_intent": _json(upstream_case_blobs["design_intent.json"]),
                "question_intent": _json(upstream_case_blobs["question_intent.json"]),
                "public_protocol": upstream_case_blobs["protocol.txt"].decode("utf-8-sig")}):
        raise ValueError("Upstream case input values differ from their retained originals")
    ledger = _upstream_ledger(upstream_blobs, failed)
    prepared = prepare_question_cycle(_json(blobs["snapshot.json"]), failed, plan.get("cycle_plan"))
    if prepared["binding"]["upstream_kind"] != kind:
        raise ValueError("Explicit upstream kind differs from the real terminal report")
    prepared["binding"]["upstream_wire_audit"] = ledger
    selected = source_names(prepared["plan"])
    expected_sources = plan.get("source_sha256", {})
    if set(expected_sources) != set(selected):
        raise ValueError("Plan must freeze the complete continuation implementation")
    sources = {name: (ROOT / name).read_bytes() for name in selected}
    if any(_hash(sources[name]) != expected for name, expected in expected_sources.items()):
        raise ValueError("Frozen implementation changed; prepare a new derived case")
    from llm_transport import validate_transport, transport_fingerprint, resolve_profile
    if plan.get("transport") is None:
        raise ValueError("Continuation requires an explicit frozen transport/profile")
    transport = validate_transport(plan["transport"])
    for spec in prepared["plan"]["phases"].values():
        for key in ("model", "reader_model", "reviewer_model", "reference_auditor_model"):
            if key in spec:
                profile = resolve_profile(transport, spec[key])["profile"]
                if profile is None or not {"http_timeout_seconds", "deadline_seconds"} <= set(profile):
                    raise ValueError("Every continuation model needs an explicit profile and bounded timeouts")
    context = {"configuration": transport, "fingerprint": transport_fingerprint(transport),
        "scope": "Fresh continuation only; no prior review, grade or result cache is reused"}
    return {"plan": plan, "prepared": prepared, "blobs": blobs, "sources": sources,
        "upstream_dir": upstream_dir, "upstream_blobs": upstream_blobs,
        "upstream_case_blobs": upstream_case_blobs, "transport": context}


def _validate_frozen(case, prepared):
    for name, blob in prepared["blobs"].items():
        if (case / name).read_bytes() != blob:
            raise ValueError("Frozen continuation input changed: " + name)
    for name, blob in prepared["sources"].items():
        if (ROOT / name).read_bytes() != blob:
            raise ValueError("Frozen continuation source changed: " + name)
    for name, blob in prepared["upstream_blobs"].items():
        if (prepared["upstream_dir"] / name).read_bytes() != blob:
            raise ValueError("Frozen upstream failed-run evidence changed: " + name)
    for name, blob in prepared["upstream_case_blobs"].items():
        if (prepared["upstream_dir"].parent / name).read_bytes() != blob:
            raise ValueError("Frozen upstream case input changed: " + name)


def _reserve(path, entry):
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def run(case, *, execute=False):
    case = Path(case).resolve()
    directory = case / "execution"
    if directory.exists():
        raise FileExistsError("Execution already claimed; use a new explicit derived case")
    prepared = _prepare(case)
    data, plan = prepared["prepared"], prepared["plan"]
    cycle_plan, context = data["plan"], prepared["transport"]
    reuse_options = {}
    if "review_reuse" in cycle_plan:
        reuse_options["review_reuse_context"] = {"transport": deepcopy(context["configuration"]),
            "source_sha256": deepcopy(plan["source_sha256"])}
    lineage = {**deepcopy(data["binding"]), "upstream_execution_directory": str(prepared["upstream_dir"]),
        "upstream_sha256": {name: _hash(blob) for name, blob in prepared["upstream_blobs"].items()}}
    if not execute:
        return {"ready": True, "execute": False, "calls_permitted": 0,
            "planned_max_provider_attempts": cycle_plan["max_provider_attempts"],
            "lineage": lineage, "execution_transport": context, "result_scope": "research_only"}
    directory.mkdir(exist_ok=False)
    clean, report, wire_dispatches = deepcopy, None, 0
    lock = RLock()
    try:
        for name, blob in prepared["blobs"].items():
            _write_bytes(directory / "inputs" / name, blob)
        for name, blob in prepared["sources"].items():
            _write_bytes(directory / "source_snapshot" / name, blob)
        save(directory / "manifest.json", {"version": PLAN_VERSION, "started_unix": time.time(),
            "case": str(case), "run_kind": "derived_question_cycle", "result_scope": "research_only",
            "runtime": {"python_executable": sys.executable, "python_version": sys.version},
            "publication_effect": "none", "official_release": False, "lineage": lineage,
            "input_sha256": {name: _hash(blob) for name, blob in prepared["blobs"].items()},
            "source_sha256": {name: _hash(blob) for name, blob in prepared["sources"].items()},
            "execution_transport": context, "max_provider_attempts": cycle_plan["max_provider_attempts"],
            "automatic_retry": False, "overwrite_or_restart": False, "prior_results_reused": False,
            **({"prior_results_reused_scope": "No upstream-run review, grade or cache is reused",
                "review_reuse_policy": cycle_plan["review_reuse"]} if reuse_options else {})})
        _validate_frozen(case, prepared)
        call, record, clean = provider(directory, transport=context["configuration"])

        def before_call(attempt):
            _validate_frozen(case, prepared)
            _reserve(directory / "reservations.jsonl", attempt)

        def counted_call(step, messages, **params):
            nonlocal wire_dispatches
            with lock:
                wire_dispatches += 1
            return call(step, messages, **params)

        def checkpoint(event):
            name = event["phase"]
            if name not in QUESTION_CYCLE_PHASES:
                raise ValueError("Unexpected continuation phase checkpoint")
            phase_dir = directory / "phases" / name
            phase_dir.mkdir(parents=True, exist_ok=False)
            save(phase_dir / "report.json", clean(event["phase_report"]))
            save(phase_dir / "case_checkpoint.json", clean({**event["case_report"],
                "lineage": lineage, "execution_transport": context}))

        report = run_agent_question_cycle(data["original_snapshot"], data["upstream_report"], plan=cycle_plan,
            chat_json=counted_call, record=record, on_phase=checkpoint, before_call=before_call, **reuse_options)
        report.update(lineage=lineage, execution_transport=context)
        _validate_frozen(case, prepared)
        _exports(directory, report, clean)
    except Exception as exc:
        report = getattr(exc, "report", None) or report
        if report is not None:
            report.update(lineage=lineage, execution_transport=context)
            save(directory / "failed_report.json", clean(report))
        save(directory / "failure.json", {"error_type": type(exc).__name__,
            "status": "failed_execution_preserved", "result_scope": "research_only",
            "run_kind": "derived_question_cycle", "lineage": lineage,
            "new_provider_dispatches": wire_dispatches, "official_release": False})
        raise
    finally:
        budget = deepcopy(report["budget"]) if report else {"max_provider_attempts": cycle_plan["max_provider_attempts"],
            "actual_provider_attempts": wire_dispatches, "phase_provider_attempts": {}}
        save(directory / "attempt_count.json", {**budget, "new_provider_dispatches": wire_dispatches,
            "upstream_budget": data["binding"]["upstream_budget"],
            "lineage_total_provider_dispatches": data["binding"]["upstream_provider_attempts"] + wire_dispatches,
            "wire_count_authority": "attempts.jsonl request records; dispatch count can include local provider rejection"})
        try:
            _validate_frozen(case, prepared)
            freeze_status = {"status": "matches", "source_count": len(prepared["sources"])}
        except Exception as exc:
            freeze_status = {"status": "mismatch", "error_type": type(exc).__name__, "message": str(exc)}
        save(directory / "post_run_freeze_verification.json", freeze_status)
    return {"case": str(case), "execution_directory": str(directory), "execute": True,
        "execution": report["execution"], "actual_provider_attempts": report["budget"]["actual_provider_attempts"],
        "upstream_provider_attempts": data["binding"]["upstream_provider_attempts"],
        "lineage_total_provider_dispatches": data["binding"]["upstream_provider_attempts"] + wire_dispatches,
        "max_provider_attempts": cycle_plan["max_provider_attempts"], "result_scope": "research_only",
        "official_release": False, "execution_transport": context}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.case, execute=args.execute), ensure_ascii=False))


if __name__ == "__main__":
    main()
