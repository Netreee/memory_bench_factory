"""Run one explicitly frozen, bounded prospective research case.

Dry run validates without importing provider configuration. --execute claims a
new execution directory; an interrupted or failed case is never overwritten or
automatically restarted. Phase checkpoints and provider traces remain separate
from fallible quality findings and later material/scoring acceptance.
Team plans may opt into review_reuse='same-input-within-run/v1'. The runner
passes frozen transport/source conditions; missing proof retains fresh reviews.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.agent_factory import run_agent_case, AgentCaseError, case_phases, _plan
from pipeline.material_series import prepare_material_series
from tools.run_bc_case import provider, save

PLAN_VERSION = "agent-case-plan/v1"
TEAM_PLAN_VERSION = "agent-case-plan/v2"
INPUTS = ("plan.json", "seed.json", "design_intent.json", "question_intent.json", "protocol.txt")
SOURCES = ("tools/run_agent_case.py", "pipeline/agent_factory.py", "pipeline/agent_editing.py",
    "pipeline/material_series.py", "pipeline/material_proposals.py", "pipeline/question_series.py",
    "pipeline/question_proposals.py", "pipeline/semantic_review.py", "pipeline/quality_workflow.py",
    "eval/semantic_judge.py", "eval/answer_task_review.py", "pipeline/reference_locations.py",
    "eval/grading.py", "eval/provenance.py", "tools/run_bc_case.py",
    "config.py", "llm_trace.py")
TEAM_SOURCES = SOURCES + ("pipeline/material_team.py", "pipeline/question_set_review.py")


def source_names(factory_plan, *, transport=None):
    """Freeze optional review implementation only when the plan selects it."""
    names = TEAM_SOURCES if factory_plan.get("material_mode") == "team/v1" else SOURCES
    if any(spec.get("reference_auditor_model") is not None
           for spec in factory_plan["phases"].values()):
        names += ("pipeline/reference_audit.py", "pipeline/reference_locations.py")
    if transport is not None:
        names += ("llm_transport.py",)
    return tuple(dict.fromkeys(names))


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _prepare(case):
    blobs = {name: (case / name).read_bytes() for name in INPUTS}
    data = {name: json.loads(blob.decode("utf-8-sig")) for name, blob in blobs.items() if name.endswith(".json")}
    plan = data["plan.json"]
    if plan.get("version") not in {PLAN_VERSION, TEAM_PLAN_VERSION} or plan.get("result_scope") != "research_only":
        raise ValueError("An explicit versioned research_only case plan is required")
    factory_plan = _plan(plan.get("factory_plan"))
    team_mode = factory_plan.get("material_mode") == "team/v1"
    if plan["version"] != (TEAM_PLAN_VERSION if team_mode else PLAN_VERSION):
        raise ValueError("Case plan v2 requires material_mode=team/v1; legacy mode uses v1")
    expected_inputs = plan.get("input_sha256", {})
    if set(expected_inputs) != set(INPUTS) - {"plan.json"}:
        raise ValueError("Plan must freeze every seed, intent and protocol input")
    for name, expected in expected_inputs.items():
        if _hash(blobs[name]) != expected:
            raise ValueError("Frozen case input changed: " + name)
    expected_sources = plan.get("source_sha256", {})
    selected_sources = source_names(factory_plan, transport=plan.get("transport"))
    if set(expected_sources) != set(selected_sources):
        raise ValueError("Plan must freeze the complete implementation source set")
    sources = {name: (ROOT / name).read_bytes() for name in selected_sources}
    for name, expected in expected_sources.items():
        if _hash(sources[name]) != expected:
            raise ValueError("Frozen implementation changed; create an explicit derived case: " + name)
    # Validate only after the optional implementation has been hash-checked.
    # The absent/None path never imports llm_transport or provider configuration.
    if plan.get("transport") is not None:
        from llm_transport import validate_transport
        plan["transport"] = validate_transport(plan["transport"])
    protocol = blobs["protocol.txt"].decode("utf-8-sig")
    if not protocol.strip():
        raise ValueError("A nonempty explicit public protocol is required")
    prepared = prepare_material_series(data["seed.json"], data["design_intent.json"],
        model=factory_plan["phases"]["material_generation"]["model"],
        batch_specs=factory_plan["material_batches"], public_protocol=protocol)
    return plan, factory_plan, data, protocol, blobs, sources, prepared


def _write_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def _exports(directory, report, clean):
    save(directory / "report.json", clean(report))
    for key, name in (("original_snapshot", "original_snapshot.json"),
                      ("current_snapshot", "snapshot.json"), ("receipt", "receipt.json"),
                      ("original_corpus", "original_material_snapshot.json"),
                      ("current_corpus", "material_snapshot.json")):
        if report.get(key) is not None:
            save(directory / name, clean(report[key]))
    collection = (report.get("receipt") or {}).get("collection_assessment")
    if collection is not None:
        save(directory / "collection_assessment.json", clean(collection))
    save(directory / "quality_status.json", clean({
        "result_scope": "research_only", "publication_effect": "none", "official_release": False,
        "execution": report["execution"], "material_ready": report.get("material_ready", False),
        "material_acceptance": report.get("material_acceptance"),
        "scoring_ready": (report.get("receipt") or {}).get("scoring_ready", False),
        "semantic_status": report.get("semantic_status", "No complete independent quality disposition"),
        "interpretation": "Execution and transport completion do not certify material quality, question difficulty, or scoring readiness."}))


def run(case, *, execute=False):
    case = Path(case).resolve()
    directory = case / "execution"
    if directory.exists():
        raise FileExistsError("Execution already claimed; create a new explicit derived case")
    plan, factory_plan, data, protocol, blobs, sources, prepared = _prepare(case)
    transport = plan.get("transport")
    transport_context = None
    if transport is not None:
        from llm_transport import transport_fingerprint
        transport_context = {"configuration": deepcopy(transport), "fingerprint": transport_fingerprint(transport),
            "scope": "This fresh case execution only; not a cross-profile replay or cache identity"}
    context_fields = {"execution_transport": transport_context} if transport_context is not None else {}
    reuse_options = {}
    if "review_reuse" in factory_plan:
        reuse_options["review_reuse_context"] = {"transport": deepcopy(transport),
            "source_sha256": deepcopy(plan["source_sha256"])}
        context_fields["review_reuse_policy"] = factory_plan["review_reuse"]
    if not execute:
        return {"case": str(case), "ready": True, "execute": False, "calls_permitted": 0,
            "planned_max_provider_attempts": factory_plan["max_provider_attempts"],
            "material_generation_binding": prepared["binding"], "result_scope": "research_only", **context_fields}
    directory.mkdir(exist_ok=False)
    for name, blob in blobs.items():
        _write_bytes(directory / "inputs" / name, blob)
    for name, blob in sources.items():
        _write_bytes(directory / "source_snapshot" / name, blob)
    save(directory / "manifest.json", {"plan_version": plan["version"], "started_unix": time.time(),
        "case": str(case), "result_scope": "research_only", "publication_effect": "none",
        "input_sha256": {name: _hash(blob) for name, blob in blobs.items()},
        "source_sha256": {name: _hash(blob) for name, blob in sources.items()},
        "max_provider_attempts": factory_plan["max_provider_attempts"],
        "automatic_retry": False, "overwrite_or_restart": False, **context_fields})
    clean = deepcopy
    report = None
    try:
        call, record, clean = provider(directory) if transport is None else provider(directory, transport=transport)
        def checkpoint(event):
            name = event["phase"]
            active_phases = case_phases(factory_plan)
            subphase = None
            if factory_plan.get("material_mode") == "team/v1" and name.startswith("material_team."):
                from pipeline.material_team import PHASES as team_phases
                subphase = name.removeprefix("material_team.")
                if subphase not in team_phases:
                    raise ValueError("Unexpected material team subphase checkpoint")
            elif name not in active_phases:
                raise ValueError("Unexpected agent phase checkpoint")
            phase_dir = (directory / "phases" / "material_team" / subphase if subphase
                         else directory / "phases" / name)
            phase_dir.mkdir(parents=True, exist_ok=True)
            if (phase_dir / "report.json").exists() or (phase_dir / "case_checkpoint.json").exists():
                raise FileExistsError("Phase checkpoint already exists; refusing overwrite")
            save(phase_dir / "report.json", clean(event["phase_report"]))
            save(phase_dir / "case_checkpoint.json", clean({**event["case_report"], **context_fields}))
            if subphase:
                save(phase_dir / "material_team_checkpoint.json", clean(event["material_team_report"]))
        report = run_agent_case(data["seed.json"], data["design_intent.json"], data["question_intent.json"],
            protocol, plan=factory_plan, chat_json=call, record=record, on_phase=checkpoint, **reuse_options)
        report.update(deepcopy(context_fields))
        _exports(directory, report, clean)
    except Exception as exc:
        partial = getattr(exc, "report", None)
        if partial is not None:
            report = partial
            report.update(deepcopy(context_fields))
            save(directory / "failed_report.json", clean(partial))
        save(directory / "failure.json", {"error_type": type(exc).__name__,
            "status": "failed_execution_preserved", "result_scope": "research_only",
            "publication_effect": "none", "official_release": False,
            "available_phase_directories": sorted(p.name for p in (directory / "phases").glob("*") if p.is_dir())})
        raise
    finally:
        if report is not None:
            save(directory / "attempt_count.json", clean(report["budget"]))
    return {"case": str(case), "execution_directory": str(directory), "execute": True,
            "execution": report["execution"], "actual_provider_attempts": report["budget"]["actual_provider_attempts"],
            "max_provider_attempts": factory_plan["max_provider_attempts"], "result_scope": "research_only",
            "official_release": False, **context_fields}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.case, execute=args.execute), ensure_ascii=False))


if __name__ == "__main__":
    main()
