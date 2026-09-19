"""Read and export existing agent research artifacts, without provider calls.

This module consumes current validation functions. It never converts a research
case to a legacy world, assigns typed labels, or decides semantic correctness.
External audits are linked opinions by default. Explicit held mode permits only
an exact, attributed scored-to-held view; original grades remain unchanged.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

VERSION = "agent-research-dataset/v1"
INSPECT_VERSION = "agent-artifact-inspection/v1"


def _read(path):
    return json.loads(Path(path).read_bytes().decode("utf-8-sig"))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _source(path):
    path = Path(path).resolve(strict=True)
    return {"path": str(path), "sha256": _sha(path)}


def _provider_json(text):
    # Mirror config._strip_code_fence without importing config/.env/SDK. The
    # source-case validator also binds config.py; strict mode parses this whole
    # cleaned JSON, rather than trying substring or repair fallbacks.
    text = (text or "").strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return json.loads(match.group(1).strip() if match else text)


def load_generation_source(case):
    """Reuse the one existing final-package validator and its exact tuple API."""
    from tools.agent_pipeline import _upstream
    return _upstream(Path(case).resolve(strict=True))


def _external_opinion(path, raw_path, snapshot_id):
    if path is None:
        return None
    value = _read(path)
    if (value.get("version") != "externally-audited-evaluation-view/v1"
            or value.get("snapshot_id") != snapshot_id
            or value.get("result_scope") != "research_only"
            or value.get("official_release") is not False
            or value.get("automated_source", {}).get("sha256") != _sha(raw_path)):
        raise ValueError("External opinion does not bind this exact raw evaluation")
    return {"artifact": _source(path), "source_report_sha256": _sha(raw_path),
        "authority": "external attribution in the supplied artifact, not a provider verdict",
        "scope": "linked_opinion_only", "applied_to_scores_or_filter": False,
        "declared_holds": deepcopy(value.get("external_holds", [])),
        "interpretation": value.get("interpretation"),
        "limit": "v1 verifies linkage only; it does not validate or adopt the external result modifications."}


def _execution_items(directory, plan, snapshot, public, context, report, runner, review_hash):
    """Bind saved summaries to original item files, actual wires and closure."""
    from eval.reassessment import ReassessmentTracker
    from eval.grading import is_scored
    from eval.provenance import provenance_issue
    from llm_transport import transport_fingerprint
    events = [json.loads(line) for line in (directory / "attempts.jsonl").read_text(encoding="utf-8").splitlines()]
    calls = [json.loads(line) for line in (directory / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    requests = [e for e in events if e.get("event") == "request"]
    parsed = {e["operation_id"]: e for e in events if e.get("event") == "json_result"}
    responses = {e["call_id"]: e for e in events if e.get("event") == "response"}
    used = set()
    full, truncated, _ = runner.build_full_context(public, plan["full_context_char_budget"])
    if truncated: raise ValueError("Saved FullContext input exceeds the declared budget")

    def match_wire(step, messages, output, *, allow_transport_error=False):
        matches = [e for e in requests if e["call_id"] not in used and e.get("step") == step and e.get("messages") == messages]
        if not matches: raise ValueError("Item has no matching actual request: " + step)
        request = matches[0]
        spec = (plan["judge"] if step in {"semantic_judge.answer", "agent_editing.independent_answer_task_review"}
                else next((s for s in plan["systems"] if step == "solver." + s["name"]), None))
        if spec is None: raise ValueError("Undeclared execution role")
        parameters = runner.build_request_parameters(model=spec["model"], max_tokens=spec["max_tokens"],
            temperature=0.0, top_p=1.0, transport=plan["transport"])
        parameters["deadline_s"] = runner.resolve_profile(plan["transport"], spec["model"])["profile"]["deadline_seconds"]
        if request.get("model") != spec["model"] or request.get("parameters") != parameters:
            raise ValueError("Actual role model/token parameters differ from the frozen plan")
        result = parsed.get(request["operation_id"])
        response = responses.get(request["call_id"])
        if allow_transport_error and result is None and response is None:
            errors = [e for e in events if e.get("event") == "call_error" and e.get("call_id") == request["call_id"]
                      and e.get("operation_id") == request["operation_id"] and e.get("step") == step]
            json_errors = [e for e in events if e.get("event") == "json_error"
                           and e.get("operation_id") == request["operation_id"] and e.get("step") == step]
            if len(errors) == 1 and len(json_errors) == 1:
                used.add(request["call_id"])
                return request
            raise ValueError("Failed solver lacks its exact actual transport error trace")
        if result is None or response is None: raise ValueError("Item lacks an actual parsed response")
        raw = _provider_json(response["response"]["choices"][0]["content"])
        if raw != result.get("parsed"): raise ValueError("Parsed response differs from actual provider bytes")
        normalized = (runner.normalize_grading_layout(raw)[0] if step == "semantic_judge.answer"
                      and plan.get("grading_response_layout", "strict/v1") != "strict/v1" else raw)
        if normalized != output: raise ValueError("Finished output differs from the actual provider response")
        used.add(request["call_id"])
        return request

    recreated = {spec["name"]: {"records": []} for spec in plan["systems"]}
    for index, question in enumerate(snapshot["questions"]):
        for system_index, spec in enumerate(plan["systems"]):
            path = directory / "items" / f"{index + 1:03d}_{system_index + 1}.json"
            if not path.is_file():
                if report.get("execution", {}).get("status") == "completed":
                    raise ValueError("Completed evaluation is missing an original item artifact")
                row = runner.unscored_row(question, context, "execution_stopped_before_item")
            else:
                row = _read(path)
                if any(row.get(k) != v for k, v in question.items()) or provenance_issue(row, question, context):
                    raise ValueError("Original item differs from its source question/reference/context")
                if "solver_identity" in row:
                    expected = {"model": spec["model"], "transport_fingerprint": transport_fingerprint(plan["transport"]),
                        "answer_prompt_hash": runner.fingerprint(runner.ANSWER_SYSTEM), "max_tokens": spec["max_tokens"],
                        "implementation_version": runner.ANSWER_VERSION}
                    if row["solver_identity"] != expected:
                        raise ValueError("Original item solver identity differs from the declared configuration")
                    messages = [{"role": "system", "content": runner.ANSWER_SYSTEM}, {"role": "user", "content":
                        json.dumps({"public_question": question["question"], "public_material": full,
                                    "public_protocol": snapshot["protocol"]}, ensure_ascii=False, allow_nan=False)}]
                    failed_solver = (row.get("execution_status") == "error" and row.get("pred") == ""
                        and row.get("correct") is None and row.get("judgement", {}).get("verdict") == "error")
                    match_wire("solver." + spec["name"], messages, {"answer": row["pred"]},
                               allow_transport_error=failed_solver)
                elif is_scored(row):
                    raise ValueError("Scored item has no explicit solver identity")
                grade = row.get("original_judgement", row.get("judgement", {}))
                if grade.get("path") == "llm_semantic" and grade.get("execution_status") != "error":
                    finished = [e for e in calls if e.get("step") == "semantic_judge.answer" and e.get("event") == "finished"
                                and e.get("source_qid") == question["qid"] and e.get("answer_hash") == grade.get("answer_hash")
                                and e.get("judgement") == grade]
                    if not finished: raise ValueError("Item judgement differs from its original finished grading artifact")
                    event = finished[0]
                    match_wire("semantic_judge.answer", event["messages"], event["output"])
                    if is_scored(row) and (grade["verdict"] != event["output"]["answer_verdict"]
                                          or grade["reason"] != event["output"]["reasoning"]):
                        raise ValueError("Scored judgement differs from the original verdict/reason")
                    if runner.scoring_config(plan)["reader_enabled"]:
                        audit = grade["answer_task_review"]
                        reader = [e for e in calls if e.get("step") == "agent_editing.independent_answer_task_review"
                                  and e.get("event") == "finished" and e.get("binding") == audit["binding"]
                                  and e.get("output") == audit["raw_output"]]
                        if not reader: raise ValueError("Item lacks its original independent reader artifact")
                        match_wire("agent_editing.independent_answer_task_review", reader[0]["messages"], audit["raw_output"])
            recreated[spec["name"]]["records"].append(row)
    tracker = ReassessmentTracker(context, review_hash)
    closure = tracker.close({system: result["records"] for system, result in recreated.items()})
    if (any(value["records"] != report["results"][system]["records"]
            for system, value in recreated.items()) or closure != report.get("reassessment_closure")):
        raise ValueError("Aggregate results/closure differ from original items and the existing closure operation")
    if len(used) != len(requests):
        raise ValueError("Evaluation contains unbound actual requests")
    return closure


def _held_results(path, raw_report, external, snapshot_id, source_root):
    """Accept only exact attributed scored-to-held copies; never a new grade."""
    from eval.grading import is_scored
    value = _read(path)
    expected = deepcopy(raw_report["results"])
    systems = set(expected)
    if set(value.get("results", {})) != systems:
        raise ValueError("External held view changes the system denominator")
    holds = value.get("external_holds")
    if not isinstance(holds, list) or not holds or len({h.get("qid") for h in holds}) != len(holds):
        raise ValueError("Explicit unique external hold declarations required")
    bound = []
    for marker in holds:
        if (marker.get("version") != "external-hold-view/v1" or marker.get("status") != "external_hold"
                or marker.get("scope") != "same_item_all_systems" or marker.get("automatic_pipeline_trigger") is not False
                or marker.get("replacement_model_grade") is not False or marker.get("api_calls") != 0):
            raise ValueError("Unsupported external hold marker")
        declaration_path = Path(marker["declaration_path"])
        if not declaration_path.is_absolute(): declaration_path = source_root / declaration_path
        if _sha(declaration_path) != marker["declaration_sha256"]:
            raise ValueError("External hold declaration bytes changed")
        declaration = _read(declaration_path)
        if (declaration.get("version") != "external-research-grade-hold/v1"
                or declaration.get("source_case_snapshot_id") != snapshot_id
                or declaration.get("qid") != marker["qid"] or declaration.get("reason") != marker.get("reason")
                or declaration.get("authority") != marker.get("authority") or declaration.get("formal_artifacts_modified") is not False):
            raise ValueError("External hold declaration identity/attribution differs")
        bound.append(_source(declaration_path))
        evidence = declaration.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("External hold requires hash-linked evidence")
        for entry in evidence:
            source = Path(entry["path"])
            if not source.is_absolute(): source = source_root / source
            if _sha(source) != entry["sha256"]:
                raise ValueError("External hold evidence bytes changed")
            bound.append(_source(source))
        for system in systems:
            rows = [r for r in expected[system]["records"] if r.get("qid") == marker["qid"]]
            if len(rows) != 1 or not is_scored(rows[0]) or rows[0].get("judgeable", True) is not True:
                raise ValueError("Hold must affect one previously scored row in every declared system")
            row = rows[0]
            row.update(original_correct=row["correct"], original_judgeable=row.get("judgeable", True),
                       correct=None, judgeable=False, external_hold=deepcopy(marker))
    if expected != value["results"]:
        raise ValueError("External held view changes more than its exact allowed disposition fields")
    original_count = next(iter(raw_report["results"].values()))["input_count"]
    if (value.get("original_question_denominator") != original_count
            or value.get("original_answer_denominator") != original_count * len(systems)
            or value.get("new_provider_calls") != 0):
        raise ValueError("External held view changes original denominators or claims new calls")
    external.update(scope="verified_external_hold_only", applied_to_scores_or_filter=True,
        evidence_files=bound,
        limit="Only exact attributed scored-to-held changes verified; this is external adjudication, not a new provider verdict or proof of its semantic correctness.")
    return {name: value["records"] for name, value in expected.items()}


def _evaluation(case, snapshot, audited_view, audit_mode):
    from tools import run_agent_evaluation as runner
    from eval.grading import is_scored
    from eval.provenance import provenance_issue
    from eval.question_filter import load_results, filter_questions

    case = Path(case).resolve(strict=True)
    plan, actual, review, calibration, context, public, grader = runner._prepare(case)
    if actual != snapshot:
        raise ValueError("Evaluation snapshot differs from the generation's actual final snapshot")
    directory = case / "execution"
    path = directory / "report.json"
    report, manifest = _read(path), _read(directory / "manifest.json")
    if (report.get("version") != plan["version"] or report.get("snapshot_id") != snapshot["snapshot_id"]
            or report.get("result_scope") != "research_only" or report.get("official_release") is not False
            or report.get("scoring_configuration") != runner.scoring_config(plan)
            or manifest.get("plan") != plan or manifest.get("evaluation_context") != context
            or manifest.get("scoring_configuration") != runner.scoring_config(plan)):
        raise ValueError("Evaluation manifest, method or result binding differs")
    for name in ("plan.json", *runner.INPUTS):
        if (case / name).read_bytes() != (directory / "inputs" / name).read_bytes():
            raise ValueError("Evaluation execution input copy differs: " + name)
    for name, expected in plan["source_sha256"].items():
        if _sha(directory / "source_snapshot" / name) != expected:
            raise ValueError("Evaluation source copy differs: " + name)
    reservations = [_read(p) for p in sorted((directory / "reservations").glob("*.json"))]
    if [x.get("attempt") for x in reservations] != list(range(1, len(reservations) + 1)):
        raise ValueError("Evaluation reservations have gaps or duplicate attempt numbers")
    ledger = runner.dispatch_ledger(directory, plan, reservations)
    if (ledger != report.get("ledger") or report.get("actual_provider_attempts") != ledger["request_attempt_events"]
            or len(reservations) > plan["max_provider_attempts"]):
        raise ValueError("Saved evaluation ledger differs from actual trace/reservations")
    systems = [s["name"] for s in plan["systems"]]
    if set(report.get("results", {})) != set(systems):
        raise ValueError("Evaluation changed the declared system denominator")
    rows = load_results(aggregate=path, systems=systems)
    closure = _execution_items(directory, plan, snapshot, public, context, report, runner,
                               grader.cache_context["reference_review_hash"])
    questions = {q["qid"]: q for q in snapshot["questions"]}
    summary, slot_rows = {}, []
    for system in systems:
        ids = [row.get("qid") for row in rows[system]]
        if (len(ids) != len(set(ids)) or any(qid not in questions for qid in ids)
                or report["results"][system].get("input_count") != len(questions)):
            raise ValueError("Evaluation has duplicate/unknown qids or changed input denominator")
        indexed = {row["qid"]: row for row in rows[system]}
        counts = {"original_slots": len(questions), "present": len(indexed), "scored": 0,
                  "correct": 0, "incorrect": 0, "unscored": 0, "missing": 0,
                  "execution_errors": 0, "incomplete_or_skipped": 0}
        for qid, question in questions.items():
            row = indexed.get(qid)
            if row is None:
                counts["missing"] += 1; counts["unscored"] += 1
                slot_rows.append({"system": system, "qid": qid, "status": "missing", "correct": None})
                continue
            issue = provenance_issue(row, question, context)
            scored = is_scored(row) and row.get("judgeable", True) is True and issue is None
            grade = row.get("judgement", {})
            if is_scored(row) and ((plan.get("scoring_policy") is not None and grade.get("scoring_configuration") != runner.scoring_config(plan))
                    or grade.get("review_hash") != grader.cache_context["reference_review_hash"]
                    or grade.get("judge_model") != plan["judge"]["model"]):
                raise ValueError("Scored record belongs to another declared method/review/model")
            counts["scored" if scored else "unscored"] += 1
            counts["execution_errors"] += row.get("execution_status") == "error"
            counts["incomplete_or_skipped"] += row.get("execution_status") == "incomplete"
            if scored: counts["correct" if row["correct"] else "incorrect"] += 1
            slot_rows.append({"system": system, "qid": qid, "status": "scored" if scored else "unscored",
                "correct": row["correct"] if scored else None, "provenance_issue": issue,
                "execution_status": row.get("execution_status"), "skip_reason": row.get("skip_reason"),
                "judge_verdict": grade.get("verdict")})
        counts["accuracy_on_scored_only"] = counts["correct"] / counts["scored"] if counts["scored"] else None
        summary[system] = counts

    kept, filtered = filter_questions(snapshot["questions"], rows,
        keep_easy_ratio=plan["keep_easy_ratio"], seed=plan["filter_seed"], expected_context=context)
    completed = report.get("execution", {}).get("status") == "completed"
    if completed and (report.get("final_audit_status") != "verified"
            or report.get("post_run_source_matches") is not True or report.get("post_run_inputs_match") is not True
            or not ledger["all_observed_wires_match"] or not ledger["complete_response_audit"]
            or report.get("filter") != filtered or report.get("kept_questions") != kept):
        raise ValueError("Completed evaluation does not reproduce its saved filter or execution audit")
    external = _external_opinion(audited_view, path, snapshot["snapshot_id"])
    raw_view = {"summary": deepcopy(summary), "slots": deepcopy(slot_rows),
                "filter": deepcopy(filtered) if completed else None}
    if audit_mode == "held":
        if not completed: raise ValueError("External held view requires complete validated raw results")
        rows = _held_results(audited_view, report, external, snapshot["snapshot_id"], runner.ROOT)
        held_qids = {h["qid"] for h in external["declared_holds"]}
        for system, counts in summary.items():
            held = [slot for slot in slot_rows if slot["system"] == system and slot["qid"] in held_qids]
            counts["external_held"] = len(held)
            for slot in held:
                counts["scored"] -= 1; counts["unscored"] += 1
                counts["correct" if slot["correct"] else "incorrect"] -= 1
                slot.update(status="externally_held", original_correct=slot["correct"], correct=None)
            counts["accuracy_on_scored_only"] = counts["correct"] / counts["scored"] if counts["scored"] else None
        kept, filtered = filter_questions(snapshot["questions"], rows, keep_easy_ratio=plan["keep_easy_ratio"],
                                          seed=plan["filter_seed"], expected_context=context)
    return {"case": str(case), "source": _source(path), "execution": deepcopy(report["execution"]),
        "view": "externally_held_view" if audit_mode == "held" else "raw_provider_results",
        "raw_view": raw_view, "systems": deepcopy(plan["systems"]), "judge": deepcopy(plan["judge"]),
        "scoring_configuration": runner.scoring_config(plan), "grading_response_layout": plan.get("grading_response_layout", "strict/v1"),
        "context": context, "ledger": ledger, "summary": summary, "slots": slot_rows,
        "reassessment_closure": deepcopy(closure), "filter_verified": completed,
        "filter": filtered if completed else None,
        "retained_qids": [q["qid"] for q in kept] if completed else None,
        "external_opinion": external, "calibration_files": calibration["files"],
        "authority": "Existing model results with validated identities; not independent semantic correctness",
        "external_audit_effect": ("exact external hold-only view; original grades unchanged" if audit_mode == "held"
                                  else "linked opinion only; raw scores/filter unchanged" if external else "not_supplied")}


def read_agent_dataset(case, *, evaluation_case=None, audited_view=None, selection="all", audit_mode="linked"):
    """Return a current, source-bound dataset view; do not write or call models."""
    from pipeline.semantic_review import visible_documents
    if selection not in {"all", "retained"}:
        raise ValueError("selection must be all or retained")
    if audit_mode not in {"linked", "held"} or (audit_mode == "held" and audited_view is None):
        raise ValueError("audit_mode must be linked, or held with an explicit audited_view")
    if audited_view is not None and evaluation_case is None:
        raise ValueError("An external opinion requires its original evaluation case")
    case = Path(case).resolve(strict=True)
    blobs, lineage = load_generation_source(case)
    snap, receipt = (json.loads(blobs[key].decode("utf-8-sig")) for key in ("snapshot", "receipt"))
    generation = json.loads(blobs["report"].decode("utf-8-sig"))
    evaluation = _evaluation(evaluation_case, snap, audited_view, audit_mode) if evaluation_case is not None else None
    if selection == "retained" and (evaluation is None or not evaluation["filter_verified"]):
        raise ValueError("retained requires a complete, verified saved evaluation; no silent all fallback")
    if selection == "retained" and audited_view is not None and audit_mode != "held":
        raise ValueError("v1 does not apply external opinions to filtering; inspect them independently")
    selected = (evaluation["retained_qids"] if selection == "retained" else [q["qid"] for q in snap["questions"]])
    questions = [q for q in snap["questions"] if q["qid"] in selected]
    documents, _ = visible_documents(snap["corpus"], include_titles=False)
    public = {"documents": [{k: deepcopy(d[k]) for k in ("doc_id", "content", "date", "session") if k in d} for d in documents],
        "protocol": snap["protocol"], "questions": [{"qid": q["qid"], "question": q["question"]} for q in questions]}
    references = [{"qid": q["qid"], "reference_present": "reference_proposal" in q,
        **({"reference_proposal": deepcopy(q["reference_proposal"])} if "reference_proposal" in q else {})} for q in questions]
    denominator = {k: deepcopy(lineage.get(k)) for k in ("requested_question_count", "original_candidate_count",
        "unreadable_candidate_count", "original_count", "current_count", "dispositions")}
    denominator.update(selected_count=len(questions), rows=deepcopy(receipt["rows"]), counts=deepcopy(receipt["counts"]))
    return {"version": VERSION, "result_scope": "research_only", "official_release": False,
        "publication_effect": "none", "source_case": str(case), "source_snapshot_id": snap["snapshot_id"],
        "original_snapshot_id": lineage["original_snapshot_id"], "selection": selection, "selected_qids": selected,
        "generation_execution": deepcopy(generation["execution"]), "public": public, "references": references,
        "denominators": denominator, "lineage": lineage, "evaluation": evaluation,
        "material_acceptance": deepcopy(lineage["material_acceptance"]),
        "validation": {"status": "current_identity_verified", "semantic_correctness_verified": False,
            "reader_version": VERSION, "reader_implementation_sha256": _sha(__file__)},
        "limits": ["Model acceptance is fallible; this export is not a published benchmark.",
            "Selection is a view over the original complete snapshot, not a new snapshot with inherited certification.",
            "Public inputs alone are for solvers; references, opinions, results and lineage are separate.",
            "No evaluation means not measured, not a zero score."]}


def inspect_agent_case(case, **options):
    """Readable historical/partial states remain visible when validation fails."""
    result = {"version": INSPECT_VERSION, "case": str(Path(case).resolve()), "provider_calls": 0,
        "result_scope": "research_only", "official_release": False}
    try:
        view = read_agent_dataset(case, **options)
        result.update(status="verified_research_view", export_ready=True,
            **{k: deepcopy(view[k]) for k in ("validation", "source_snapshot_id", "generation_execution",
                "denominators", "evaluation", "selected_qids", "limits")})
    except (ValueError, KeyError, TypeError, OSError) as exc:
        result.update(status="unverified_or_incomplete", export_ready=False,
            validation_error={"type": type(exc).__name__, "message": str(exc)})
        for name in ("execution/report.json", "execution/failed_report.json", "preparation.json"):
            path = Path(case) / name
            if path.is_file():
                try:
                    source = _read(path)
                    result["declared_state"] = {"source": _source(path), "version": source.get("version"),
                        "execution": source.get("execution"), "status": source.get("status"),
                        "authority": "unverified source declaration, not current validation"}
                except (ValueError, OSError):
                    pass
                break
    return result


def _write(path, blob):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(blob)


def export_agent_dataset(case, out_dir, **options):
    """Write a new local bundle, preserving source data and failure evidence."""
    case, out = Path(case).resolve(strict=True), Path(out_dir).resolve()
    evaluation_case = options.get("evaluation_case")
    source_directories = [case] + ([Path(evaluation_case).resolve(strict=True)] if evaluation_case is not None else [])
    if out.exists() or any(out == source or source in out.parents for source in source_directories):
        raise ValueError("Export needs a new directory outside the source case")
    view = read_agent_dataset(case, **options)
    files = {"public/material.json": _json_bytes(view["public"]["documents"]),
        "public/protocol.txt": view["public"]["protocol"].encode("utf-8"),
        "public/questions.json": _json_bytes(view["public"]["questions"]),
        "references/questions.json": _json_bytes(view["references"]),
        "audit/denominators.json": _json_bytes(view["denominators"]),
        "audit/material_acceptance.json": _json_bytes(view["material_acceptance"])}
    # Aggregate generation report embeds author prompts. Preserve its hash in
    # lineage, but do not copy it (or seed/intents) into this data handoff.
    for key in ("original_snapshot", "snapshot", "receipt", "review"):
        source = view["lineage"]["files"][key]
        if _sha(source["path"]) != source["sha256"]:
            raise ValueError("Source changed after validation: " + key)
        files["audit/source_" + key + ".json"] = Path(source["path"]).read_bytes()
    if view["evaluation"] is not None:
        evaluation = view["evaluation"]
        files["evaluation/summary.json"] = _json_bytes(evaluation)
        files["evaluation/raw_report.json"] = Path(evaluation["source"]["path"]).read_bytes()
        if evaluation["filter"] is not None:
            files["evaluation/filter.json"] = _json_bytes(evaluation["filter"])
        if evaluation["external_opinion"] is not None:
            files["evaluation/external_opinion.json"] = Path(evaluation["external_opinion"]["artifact"]["path"]).read_bytes()
    sources = list(view["lineage"]["files"].values())
    if view["evaluation"] is not None:
        sources.append(view["evaluation"]["source"])
        if view["evaluation"]["external_opinion"] is not None:
            sources.append(view["evaluation"]["external_opinion"]["artifact"])
            sources.extend(view["evaluation"]["external_opinion"].get("evidence_files", []))
    if any(_sha(x["path"]) != x["sha256"] for x in sources):
        raise ValueError("Source changed before export")
    manifest = {k: deepcopy(view[k]) for k in ("version", "result_scope", "official_release", "publication_effect",
        "source_snapshot_id", "original_snapshot_id", "selection", "selected_qids", "validation", "limits")}
    manifest.update(status="writing", ready=False, source_files=sources,
        source_artifact_copy_scope="Original generation report is hash-linked, not copied because it embeds author inputs.",
        offline_read_scope="Bundle byte integrity and source-snapshot projection; full execution revalidation needs the original case and its bound runtime/sources.",
        denominator_file="audit/denominators.json", evaluation_view=view["evaluation"]["view"] if view["evaluation"] else "not_run",
        files={name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)} for name, raw in files.items()})
    out.mkdir(parents=True, exist_ok=False)
    try:
        _write(out / "manifest.json", _json_bytes(manifest))
        for name, raw in files.items():
            _write(out / name, raw)
        if any(_sha(out / name) != spec["sha256"] for name, spec in manifest["files"].items()):
            raise ValueError("Exported bytes do not match manifest")
        manifest.update(status="ready", ready=True)
        (out / "manifest.json").write_bytes(_json_bytes(manifest))
    except Exception as exc:
        manifest.update(status="failed", ready=False, error={"type": type(exc).__name__, "message": str(exc)})
        (out / "manifest.json").write_bytes(_json_bytes(manifest))
        raise
    return {"version": VERSION, "out_dir": str(out), "ready": True, "provider_calls": 0,
        "result_scope": "research_only", "official_release": False, "selected_count": len(view["selected_qids"]),
        "manifest_sha256": _sha(out / "manifest.json")}


def read_exported_dataset(directory):
    """Read a portable bundle without re-executing source validation or models.

    This verifies saved bytes and their projection, not a new execution audit or
    a signature/authenticity claim. The recorded validation remains attributed
    to the exporter and its implementation hash.
    """
    root = Path(directory).resolve(strict=True)
    manifest = _read(root / "manifest.json")
    if (manifest.get("version") != VERSION or manifest.get("status") != "ready"
            or manifest.get("ready") is not True or manifest.get("result_scope") != "research_only"
            or manifest.get("official_release") is not False or manifest.get("publication_effect") != "none"):
        raise ValueError("Bundle is unsupported, incomplete or not research-only")
    required = {"public/material.json", "public/protocol.txt", "public/questions.json",
        "references/questions.json", "audit/denominators.json", "audit/material_acceptance.json",
        "audit/source_original_snapshot.json", "audit/source_snapshot.json", "audit/source_receipt.json", "audit/source_review.json"}
    files = manifest.get("files")
    if not isinstance(files, dict) or not required <= set(files):
        raise ValueError("Bundle manifest lacks required files")
    for name, identity in files.items():
        target = (root / name).resolve(strict=True)
        if target == root or root not in target.parents or not target.is_file():
            raise ValueError("Bundle path must remain inside the bundle")
        if _sha(target) != identity.get("sha256") or target.stat().st_size != identity.get("bytes"):
            raise ValueError("Bundle file changed: " + name)
    snapshot = _read(root / "audit/source_snapshot.json")
    original = _read(root / "audit/source_original_snapshot.json")
    from pipeline.quality_workflow import validate_snapshot
    validate_snapshot(original)
    validate_snapshot(snapshot, parent=original)
    source_hashes = {entry["sha256"] for entry in manifest["source_files"]}
    for key in ("original_snapshot", "snapshot", "receipt", "review"):
        if _sha(root / ("audit/source_" + key + ".json")) not in source_hashes:
            raise ValueError("Copied source bytes differ from their saved source hash")
    if (snapshot.get("snapshot_id") != manifest.get("source_snapshot_id")
            or original.get("snapshot_id") != manifest.get("original_snapshot_id")):
        raise ValueError("Bundle snapshot identity differs")
    selected = manifest.get("selected_qids")
    source_ids = [q["qid"] for q in snapshot["questions"]]
    if (not isinstance(selected, list) or len(set(selected)) != len(selected)
            or selected != [qid for qid in source_ids if qid in selected]
            or manifest.get("selection") not in {"all", "retained"}
            or (manifest["selection"] == "all" and selected != source_ids)):
        raise ValueError("Bundle selected qids differ from source order or selection")
    questions = [q for q in snapshot["questions"] if q["qid"] in selected]
    public_questions = [{"qid": q["qid"], "question": q["question"]} for q in questions]
    references = [{"qid": q["qid"], "reference_present": "reference_proposal" in q,
        **({"reference_proposal": q["reference_proposal"]} if "reference_proposal" in q else {})} for q in questions]
    from pipeline.semantic_review import visible_documents
    documents, _ = visible_documents(snapshot["corpus"], include_titles=False)
    material = [{k: d[k] for k in ("doc_id", "content", "date", "session") if k in d} for d in documents]
    if (_read(root / "public/material.json") != material
            or (root / "public/protocol.txt").read_bytes() != snapshot["protocol"].encode("utf-8")
            or _read(root / "public/questions.json") != public_questions
            or _read(root / "references/questions.json") != references):
        raise ValueError("Bundle public/reference projection differs from the actual source snapshot")
    denominators = _read(root / "audit/denominators.json")
    receipt = _read(root / "audit/source_receipt.json")
    if (denominators.get("original_count") != len(original["questions"])
            or denominators.get("current_count") != len(snapshot["questions"])
            or denominators.get("selected_count") != len(questions)
            or denominators.get("rows") != receipt["rows"] or denominators.get("counts") != receipt["counts"]):
        raise ValueError("Bundle denominator differs from full source snapshots/receipt")
    evaluation = _read(root / "evaluation/summary.json") if "evaluation/summary.json" in files else None
    if evaluation is not None:
        raw_path = root / "evaluation/raw_report.json"
        if (_sha(raw_path) != evaluation["source"]["sha256"]
                or evaluation["source"]["sha256"] not in source_hashes
                or _read(raw_path).get("snapshot_id") != snapshot["snapshot_id"]):
            raise ValueError("Copied raw evaluation differs from its source binding")
        if evaluation.get("external_opinion") is not None:
            opinion_path = root / "evaluation/external_opinion.json"
            if (_sha(opinion_path) != evaluation["external_opinion"]["artifact"]["sha256"]
                    or _read(opinion_path).get("automated_source", {}).get("sha256") != _sha(raw_path)):
                raise ValueError("Copied external opinion differs from its bound raw evaluation")
    if manifest["selection"] == "retained" and (evaluation is None or evaluation.get("filter_verified") is not True
            or evaluation.get("retained_qids") != selected
            or (evaluation.get("external_opinion") is not None and evaluation["external_opinion"].get("scope") != "verified_external_hold_only")):
        raise ValueError("Retained selection lacks its recorded raw filter")
    return {"version": VERSION, "result_scope": "research_only", "official_release": False,
        "public": {"documents": material, "protocol": snapshot["protocol"], "questions": public_questions},
        "references": references, "denominators": denominators, "evaluation": evaluation,
        "manifest": manifest,
        "read_validation": {"bytes_and_projection_verified": True, "execution_revalidated": False,
            "semantic_correctness_verified": False, "source_validation": manifest["validation"]}}
