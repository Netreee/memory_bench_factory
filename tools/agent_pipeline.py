"""Prepare and run explicitly bounded agent cases using the existing runners.

Paths inside a spec are relative to that spec. Preparation makes no model calls.
Generation and evaluation remain separate research runs; --execute is explicit.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GENERATION_SPEC = "agent-generation-spec/v1"
EVALUATION_SPEC = "agent-evaluation-spec/v1"
POLICY_GENERATION_SPEC = "agent-generation-spec/v2"
POLICY_EVALUATION_SPEC = "agent-evaluation-spec/v2"
PREPARATION_VERSION = "agent-case-preparation/v1"
GENERATION_FILES = {"seed": "seed.json", "design_intent": "design_intent.json",
                    "question_intent": "question_intent.json", "public_protocol": "protocol.txt"}
EVALUATION_SETTINGS = {"systems", "judge", "transport", "max_provider_attempts", "keep_easy_ratio",
                       "filter_seed", "full_context_char_budget", "max_input_chars"}
EVALUATION_OPTIONAL_SETTINGS = {"grading_response_layout"}


def _hash(blob):
    return hashlib.sha256(blob).hexdigest()


def _json(blob):
    def invalid(value):
        raise ValueError("Nonfinite JSON value: " + value)
    return json.loads(blob.decode("utf-8-sig"), parse_constant=invalid)


def _read(path):
    return _json(Path(path).read_bytes())


def _save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _path(value, base, *, directory=False):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Every input path must be a nonempty string")
    path = Path(value)
    path = (path if path.is_absolute() else base / path).resolve(strict=True)
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError("Input has the wrong file/directory type: " + str(path))
    return path


def _spec(path, version, fields):
    path = Path(path).resolve(strict=True)
    blob = path.read_bytes()
    value = _json(blob)
    if not isinstance(value, dict) or value.get("version") != version or set(value) != {"version", *fields}:
        raise ValueError("Unexpected or missing fields for " + version)
    return path, blob, value


def _write_bytes(path, blob):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(blob)


def _finish_preparation(case, kind, spec_path, spec_blob, files, plan, runner):
    case = Path(case).resolve()
    case.mkdir(parents=True, exist_ok=False)
    state = {"version": PREPARATION_VERSION, "kind": kind, "status": "preparing", "ready": False,
             "spec_path": str(spec_path), "spec_sha256": _hash(spec_blob),
             "model_requests": 0, "official_release": False}
    try:
        _save(case / "preparation.json", state)
        _write_bytes(case / "spec.original.json", spec_blob)
        for name, blob in files.items():
            _write_bytes(case / name, blob)
        _save(case / "plan.json", plan)
        dry_run = runner(case, execute=False)
        if dry_run.get("ready") is not True or dry_run.get("calls_permitted") != 0:
            raise ValueError("Existing runner did not confirm a zero-call ready plan")
        state.update(status="ready", ready=True, plan_sha256=_hash((case / "plan.json").read_bytes()),
                     dry_run=dry_run)
        _save(case / "preparation.json", state)
    except Exception as exc:
        state.update(status="failed", ready=False, error_type=type(exc).__name__, error=str(exc))
        _save(case / "preparation.json", state)
        raise
    return {"case": str(case), "kind": kind, "ready": True, "execute": False,
            "calls_permitted": 0, "dry_run": dry_run}


def prepare_generation(spec, case):
    from tools import run_agent_case as runner

    policy_mode = _read(spec).get("version") == POLICY_GENERATION_SPEC
    fields = {*GENERATION_FILES, "factory_plan", "transport"}
    if policy_mode:
        fields.add("scoring_policy")
    spec_path, blob, value = _spec(spec, POLICY_GENERATION_SPEC if policy_mode else GENERATION_SPEC, fields)
    if Path(case).exists():
        raise FileExistsError("Case already exists; choose a new directory")
    paths = {key: _path(value[key], spec_path.parent) for key in GENERATION_FILES}
    original_bytes = {key: path.read_bytes() for key, path in paths.items()}
    files = {GENERATION_FILES[key]: raw for key, raw in original_bytes.items()}
    public_policy = None
    if policy_mode:
        from eval.answer_task_review import append_scoring_policy, public_scoring_policy
        public_policy = public_scoring_policy(value["scoring_policy"])
        original_protocol = files["protocol.txt"]
        files["protocol.txt"] = append_scoring_policy(original_protocol.decode("utf-8-sig"),
            value["scoring_policy"]).encode("utf-8")
        files["protocol.original.txt"] = original_protocol
    # Existing preparation owns all semantic-free schema, model and budget checks.
    factory_plan = deepcopy(value["factory_plan"])
    if not isinstance(factory_plan, dict):
        raise ValueError("factory_plan must be an object")
    sources = runner.source_names(factory_plan, transport=value["transport"])
    plan = {"version": runner.TEAM_PLAN_VERSION if factory_plan.get("material_mode") == "team/v1" else runner.PLAN_VERSION,
            "result_scope": "research_only", "factory_plan": factory_plan, "transport": deepcopy(value["transport"]),
            "input_sha256": {name: _hash(files[name]) for name in GENERATION_FILES.values()},
            "source_sha256": {name: _hash((ROOT / name).read_bytes()) for name in sources},
            "preparation": {"version": PREPARATION_VERSION, "spec_sha256": _hash(blob),
                "inputs": {key: {"path": str(path), "sha256": _hash(original_bytes[key])}
                           for key, path in paths.items()}}}
    if policy_mode:
        plan["preparation"].update(public_scoring_policy=public_policy,
            original_protocol_sha256=_hash(original_protocol), effective_protocol_sha256=_hash(files["protocol.txt"]))
    return _finish_preparation(case, "generation", spec_path, blob, files, plan, runner.run)


def _upstream(source_case):
    """Select the actual final package and retain its original denominator."""
    from eval.provenance import digest
    from pipeline.quality_workflow import validate_snapshot, make_receipt

    execution = source_case / "execution"
    paths = {"report": execution / "report.json", "original_snapshot": execution / "original_snapshot.json",
             "snapshot": execution / "snapshot.json", "receipt": execution / "receipt.json",
             "review": execution / "phases/final_review/report.json"}
    blobs = {name: path.read_bytes() for name, path in paths.items()}
    data = {name: _json(blob) for name, blob in blobs.items()}
    report, original, current, review, receipt = (data[k] for k in
        ("report", "original_snapshot", "snapshot", "review", "receipt"))
    if (report.get("version") not in {"agent-case-factory/v1", "agent-case-factory/v2", "agent-question-cycle/v1"}
            or report.get("result_scope") != "research_only" or report.get("official_release") is not False
            or report.get("execution", {}).get("status") not in {"completed", "completed_with_execution_gaps"}
            or report.get("current_snapshot") != current or report.get("original_snapshot") != original
            or report.get("receipt") != receipt or report.get("phases", {}).get("final_review") != review):
        raise ValueError("Source must be a completed agent run with its exact final package and original snapshot")
    validate_snapshot(original)
    validate_snapshot(current)
    if current.get("parent_snapshot_id") != original["snapshot_id"]:
        raise ValueError("Final snapshot does not preserve its original parent")
    acceptance = report.get("material_acceptance", {})
    if (report.get("material_ready") is not True or acceptance.get("status") != "accepted"
            or acceptance.get("authority") != "model" or acceptance.get("correctness_verified") is not False
            or acceptance.get("corpus_hash") != digest(current["corpus"])
            or acceptance.get("protocol_hash") != digest(current["protocol"])):
        raise ValueError("Final public material has no matching attributed model acceptance")
    rebuilt = make_receipt(original, current, review, material_ready=True,
                           dispositions=report.get("dispositions"))
    if any(receipt.get(key) != expected for key, expected in rebuilt.items()):
        raise ValueError("Final receipt differs from actual version-bound review and original dispositions")
    lineage = {"source_case": str(source_case), "source_version": report["version"],
        "files": {name: {"path": str(paths[name]), "sha256": _hash(raw)} for name, raw in blobs.items()},
        "original_snapshot_id": original["snapshot_id"], "current_snapshot_id": current["snapshot_id"],
        "original_count": len(original["questions"]), "current_count": len(current["questions"]),
        "requested_question_count": receipt.get("requested_question_count"),
        "original_candidate_count": receipt.get("original_candidate_count"),
        "unreadable_candidate_count": receipt.get("unreadable_candidate_count"),
        "dispositions": deepcopy(report.get("dispositions")),
        "material_acceptance": deepcopy(acceptance),
        "authority": "Frozen source lineage, not a claim of semantic correctness"}
    return blobs, lineage


def prepare_evaluation(spec, case):
    from tools import run_agent_evaluation as runner

    policy_mode = _read(spec).get("version") == POLICY_EVALUATION_SPEC
    spec_path, blob, value = _spec(spec, POLICY_EVALUATION_SPEC if policy_mode else EVALUATION_SPEC,
        {"source_case", "calibration", "evaluation_plan"})
    if Path(case).exists():
        raise FileExistsError("Case already exists; choose a new directory")
    settings = value["evaluation_plan"]
    required = EVALUATION_SETTINGS | ({"scoring_policy", "grading_method"} if policy_mode else set())
    if (not isinstance(settings, dict) or not required <= set(settings)
            or set(settings) - required - EVALUATION_OPTIONAL_SETTINGS):
        raise ValueError("evaluation_plan needs the explicit existing solver, judge, transport and budget settings")
    source_case = _path(value["source_case"], spec_path.parent, directory=True)
    source, lineage = _upstream(source_case)
    calibration = value["calibration"]
    if not isinstance(calibration, dict) or set(calibration) != {"results", "plan", "semantic_audit"}:
        raise ValueError("Calibration must specify results, plan and semantic_audit files")
    paths = {name: _path(path, spec_path.parent) for name, path in calibration.items()}
    raw = {name: path.read_bytes() for name, path in paths.items()}
    results = _json(raw["results"])
    observations = [{"control": control["control"], "name": row["name"], "verdict": row["judgement"]["verdict"]}
                    for control in results.get("outcomes", []) for row in control.get("records", [])]
    evidence = {"version": "agent-calibration-evidence/v1",
        "files": {name: {"path": str(path), "sha256": _hash(raw[name])} for name, path in paths.items()},
        "reviewed_observations": observations}
    files = {"snapshot.json": source["snapshot"], "review.json": source["review"],
             "calibration.json": (json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")}
    plan = {"version": runner.POLICY_PLAN_VERSION if policy_mode else runner.VERSION,
            "result_scope": "research_only", **deepcopy(settings),
            "runtime": runner.runtime_identity(), "source_sha256": runner.source_hashes(),
            "input_sha256": {name: _hash(data) for name, data in files.items()},
            "preparation": {"version": PREPARATION_VERSION, "spec_sha256": _hash(blob)}, "lineage": lineage}
    # Existing dry run verifies calibration against actual sources/wire/opinions,
    # full review bindings, pending items, model identities and context budgets.
    return _finish_preparation(case, "evaluation", spec_path, blob, files, plan, runner.run)


def _prepared(case, kind):
    case = Path(case).resolve(strict=True)
    marker = case / "preparation.json"
    if marker.exists():
        state = _read(marker)
        if (state.get("version") != PREPARATION_VERSION or state.get("kind") != kind
                or state.get("status") != "ready" or state.get("ready") is not True
                or state.get("plan_sha256") != _hash((case / "plan.json").read_bytes())
                or state.get("spec_sha256") != _hash((case / "spec.original.json").read_bytes())):
            raise ValueError("Case preparation is failed, incomplete or changed; choose a new case")
    if kind == "evaluation":
        for value in _read(case / "plan.json").get("lineage", {}).get("files", {}).values():
            if _hash(Path(value["path"]).read_bytes()) != value["sha256"]:
                raise ValueError("Frozen source-run lineage changed; prepare a new derived case")
    return case


def generate(case, *, execute=False):
    from tools import run_agent_case
    return run_agent_case.run(_prepared(case, "generation"), execute=execute)


def evaluate(case, *, execute=False):
    from tools import run_agent_evaluation
    return run_agent_evaluation.run(_prepared(case, "evaluation"), execute=execute)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare-generation", "prepare-evaluation"):
        command = commands.add_parser(name)
        command.add_argument("--spec", type=Path, required=True)
        command.add_argument("--case", type=Path, required=True)
    for name in ("generate", "evaluate"):
        command = commands.add_parser(name)
        command.add_argument("--case", type=Path, required=True)
        command.add_argument("--execute", action="store_true")
    for name in ("inspect", "export"):
        command = commands.add_parser(name)
        command.add_argument("--case", type=Path, required=True)
        command.add_argument("--evaluation-case", type=Path)
        command.add_argument("--audited-view", type=Path)
        command.add_argument("--audit-mode", choices=("linked", "held"), default="linked")
        command.add_argument("--selection", choices=("all", "retained"), default="all")
        if name == "export": command.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare-generation": result = prepare_generation(args.spec, args.case)
        elif args.command == "prepare-evaluation": result = prepare_evaluation(args.spec, args.case)
        elif args.command == "generate": result = generate(args.case, execute=args.execute)
        elif args.command == "evaluate": result = evaluate(args.case, execute=args.execute)
        else:
            from pipeline.agent_artifacts import inspect_agent_case, export_agent_dataset
            options = {"evaluation_case": args.evaluation_case, "audited_view": args.audited_view,
                       "selection": args.selection, "audit_mode": args.audit_mode}
            result = (inspect_agent_case(args.case, **options) if args.command == "inspect"
                      else export_agent_dataset(args.case, args.out, **options))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 1 if (result.get("execution", {}).get("status") == "failed"
                     or result.get("export_ready") is False) else 0
    except Exception as exc:
        print(json.dumps({"ready": False, "error_type": type(exc).__name__, "error": str(exc),
                          "case": str(args.case.resolve())}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
