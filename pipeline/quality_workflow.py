"""Versioned coordination for the research author/reader/critic workflow.

Python tracks versions, execution and budgets. Semantic decisions belong to the
agents and remain attributed proposals. No function publishes a benchmark or
silently repairs a candidate until it passes.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import RLock

from eval.provenance import question_hash, reference_hash
from pipeline.semantic_review import (fingerprint, visible_documents, prepare_review,
    review_questions)

VERSION = "agent-quality-workflow/v1"


class FreshReviewAuditError(RuntimeError):
    """A stopped review with every available item, return and callback failure."""
    def __init__(self, message, report):
        super().__init__(message)
        self.report = deepcopy(report)


def snapshot(questions, corpus, protocol, *, parent=None):
    """Freeze the actual inputs; origin metadata never becomes reader evidence."""
    if parent is not None:
        validate_snapshot(parent)
    if not isinstance(protocol, str):
        raise ValueError("An explicit public protocol string is required")
    if isinstance(questions, dict):
        questions = questions["questions"]
    projected, seen = [], set()
    for question in questions:
        qid = question.get("qid")
        if not isinstance(qid, str) or not qid or qid in seen:
            raise ValueError("Every original item requires a distinct nonempty string qid")
        if not isinstance(question.get("question"), str) or not question["question"].strip():
            raise ValueError("A public question is required")
        if any(k in question for k in ("gt", "gold", "question_contract")):
            raise ValueError("Use explicit research reference_proposal, not legacy encoded questions")
        seen.add(qid)
        q = {"qid": qid, "question": question["question"]}
        if "reference_proposal" in question:
            q["reference_proposal"] = deepcopy(question["reference_proposal"])
        projected.append(q)
    documents, _ = visible_documents(corpus)
    body = {"questions": projected, "corpus": deepcopy(corpus), "protocol": protocol}
    public_context_hash = fingerprint({"documents": documents, "protocol": protocol})
    identities = {q["qid"]: {"question_hash": question_hash(q), "reference_hash": reference_hash(q),
        "reference_provided": "reference_proposal" in q,
        "prediction_input_hash": fingerprint({"context": public_context_hash, "question": q["question"]})}
        for q in projected}
    return {"version": VERSION, "snapshot_id": fingerprint(body),
        "parent_snapshot_id": parent["snapshot_id"] if parent else None,
        "public_context_hash": public_context_hash, "identities": identities,
        "result_scope": "research_only", "publication_effect": "none", **body}


def validate_snapshot(frozen, *, parent=None):
    """Recompute derived identities before using an imported or cached snapshot.

    File formatting and adjacent audit metadata do not matter. A valid review
    cannot legitimize prediction hashes copied from an older public context.
    """
    if not isinstance(frozen, dict):
        raise ValueError("Snapshot must be an object")
    try:
        expected = snapshot(frozen["questions"], frozen["corpus"], frozen["protocol"])
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Incomplete or unreadable snapshot") from exc
    for field in ("version", "snapshot_id", "public_context_hash", "identities"):
        if field not in frozen or fingerprint(frozen[field]) != fingerprint(expected[field]):
            raise ValueError("Snapshot content/identity mismatch: " + field)
    parent_id = frozen.get("parent_snapshot_id")
    if parent_id is not None and (not isinstance(parent_id, str) or not parent_id):
        raise ValueError("Invalid parent snapshot identity")
    if parent is not None:
        validate_snapshot(parent)
        if parent_id != parent["snapshot_id"]:
            raise ValueError("Snapshot parent does not match the supplied parent")
    return frozen


def change_impact(before, after):
    """Whole public corpus changes invalidate all predictions, not just cited docs."""
    validate_snapshot(before)
    validate_snapshot(after)
    result = {}
    for qid, identity in before["identities"].items():
        new = after["identities"].get(qid)
        if new is None:
            action = "not_selected"
        elif new["prediction_input_hash"] != identity["prediction_input_hash"]:
            action = "reanswer_and_regrade"
        elif (new["reference_hash"], new["reference_provided"]) != (identity["reference_hash"], identity["reference_provided"]):
            action = "reuse_prediction_regrade"
        else:
            action = "unchanged"
        result[qid] = {"action": action,
            "fresh_review_required": action not in {"unchanged", "not_selected"},
            "reuse_old_grade": action == "unchanged"}
    for qid in after["identities"].keys() - before["identities"].keys():
        result[qid] = {"action": "new_candidate", "fresh_review_required": True, "reuse_old_grade": False}
    return result


def fresh_review(frozen, *, reader_model, reviewer_model, chat_json, max_calls,
                 max_input_chars, max_tokens, record=None, workers=1, on_item=None,
                 reference_auditor_model=None):
    """One fresh blind context per question. Author issues cannot be passed here.

    Callback failures stop dispatch across all workers. Calls already dispatched
    may finish; their raw returns and all available partial items are retained in
    FreshReviewAuditError.report. No callback failure is a semantic item verdict.
    """
    validate_snapshot(frozen)
    if type(max_calls) is not int or max_calls < 0 or type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError("Invalid call budget or worker count")
    if not callable(chat_json) or any(cb is not None and not callable(cb) for cb in (record, on_item)):
        raise ValueError("chat_json and supplied callbacks must be callable")
    questions, corpus, protocol = frozen["questions"], frozen["corpus"], frozen["protocol"]
    kwargs = dict(reader_model=reader_model, reviewer_model=reviewer_model,
                  reference_auditor_model=reference_auditor_model)
    combined = prepare_review(questions, corpus, protocol, **kwargs)
    calls_per_item = 3 if reference_auditor_model is not None else 2
    allocations = [min(calls_per_item, max(0, max_calls - calls_per_item * i))
                   for i in range(len(questions))]
    lock, failures, dispatches = RLock(), [], []
    events = [[] for _ in questions]

    def failure(index, callback, error, **detail):
        failures.append({"item_index": index, "source_qid": questions[index]["qid"],
            "callback": callback, "error_type": type(error).__name__, "message": str(error), **detail})

    def event_record(index, event):
        with lock:
            actual = {**deepcopy(event), "source_qid": questions[index]["qid"],
                      "candidate_id": combined["items"][index]["candidate_id"]}
            # Preserve in-flight results even when the external sink is broken.
            events[index].append(actual)
            if failures:
                raise RuntimeError("An earlier review audit callback failed")
            if record:
                try:
                    record(deepcopy(actual))
                except Exception as exc:
                    failure(index, "record", exc, event=actual.get("event"), stage=actual.get("stage"))
                    raise

    def provider(index, step, messages, **params):
        with lock:
            if failures:
                raise RuntimeError("An earlier review audit callback failed; provider not dispatched")
            # This lock marks the dispatch boundary. Already dispatched work can
            # return after a different worker's sink fails; no new dispatch can.
            attempt = {"item_index": index, "source_qid": questions[index]["qid"],
                       "step": step, "status": "in_flight", "raw_output": None}
            dispatches.append(attempt)
        try:
            raw = chat_json(step, messages, **params)
        except Exception as exc:
            with lock:
                attempt.update(status="provider_error", error_type=type(exc).__name__, message=str(exc))
            raise
        with lock:
            try:
                fingerprint(raw)
                attempt["raw_output"] = deepcopy(raw)
            except (TypeError, ValueError):
                attempt["raw_output"] = {"non_json_python_repr": repr(raw)}
            attempt["status"] = "returned"
        return raw

    def one(pair):
        index, question = pair
        part = None
        with lock:
            if failures:
                return index, None, None, True
        try:
            part = review_questions([question], corpus, protocol, **kwargs,
                chat_json=lambda step, messages, **params: provider(index, step, messages, **params),
                max_calls=allocations[index], max_input_chars=max_input_chars, max_tokens=max_tokens,
                record=lambda event: event_record(index, event))
            with lock:
                if on_item and not failures:
                    try:
                        on_item(index, deepcopy(part))
                    except Exception as exc:
                        failure(index, "on_item", exc)
                        raise
            return index, part, None, False
        except Exception as exc:
            # v7 carries its partial report; v6 callback exceptions may not. In
            # both cases our event log and provider returns remain available.
            return index, part or getattr(exc, "report", None), exc, False

    with ThreadPoolExecutor(max_workers=workers) as pool:
        parts = list(pool.map(one, enumerate(questions)))
    for index, part, error, stopped in parts:
        expected = combined["items"][index]
        actual = deepcopy(part["items"][0]) if part is not None else deepcopy(expected)
        if actual["binding"] != expected["binding"] or actual["source_qid"] != expected["source_qid"]:
            raise ValueError("Partition review identity mismatch")
        actual["candidate_id"] = expected["candidate_id"]
        if failures and (error is not None or stopped):
            actual["workflow_execution"] = {"status": "audit_stopped" if stopped else "audit_error",
                "prior_execution": deepcopy(actual["execution"]),
                "partial_report_available": part is not None}
            if error is not None:
                actual["workflow_execution"].update(error_type=type(error).__name__, message=str(error))
            actual["execution"] = {"status": actual["workflow_execution"]["status"]}
            actual["review_state"] = "pending"
            actual["item_certification"] = {"status": "unverified",
                "policy": actual["item_certification"].get("policy"), "reasons": ["workflow_audit_failure"]}
        combined["items"][index] = actual
        combined["records"].extend(events[index])
    combined["calls_used"] = len(dispatches)
    combined.update({"workflow_version": VERSION, "snapshot_id": frozen["snapshot_id"],
        "result_scope": "research_only", "publication_effect": "none",
        "budget": {"max_calls": max_calls, "per_candidate_calls": allocations,
                    "max_input_chars": max_input_chars, "max_tokens": max_tokens}})
    if failures:
        combined.update(execution={"status": "audit_error"}, audit_failures=deepcopy(failures),
                        provider_dispatches=deepcopy(dispatches))
        raise FreshReviewAuditError("Review audit failed; undispatched work stopped and partial artifacts retained", combined)
    for _, _, error, _ in parts:
        if error is not None:
            raise error
    return combined


def validate_review(frozen, report):
    """Recompute binding and procedure certification, never trust a cached label."""
    validate_snapshot(frozen)
    from eval.semantic_judge import SemanticJudge
    binding = report.get("binding", {})
    expected = prepare_review(frozen["questions"], frozen["corpus"], frozen["protocol"],
        reviewer_model=binding.get("reviewer_model"), reader_model=binding.get("reader_model"),
        reference_auditor_model=binding.get("reference_auditor_model"))
    if report.get("binding") != expected["binding"] or len(report.get("items", [])) != len(expected["items"]):
        raise ValueError("Review does not bind to this input version/procedure")
    for field in ("version", "documents", "public_protocol", "source_map", "input_manifest"):
        if report.get(field) != expected[field]:
            raise ValueError("Stored review inputs differ from actual snapshot: " + field)
    documents, _ = visible_documents(frozen["corpus"])
    by_id = {}
    for original, item in zip(expected["items"], report["items"]):
        if item.get("binding") != original["binding"] or item.get("source_qid") != original["source_qid"]:
            raise ValueError("Review item identity/order mismatch")
        for field in ("question", "reference_proposal", "reference_provided", "source_identity", "reference_encoding"):
            if item.get(field) != original[field]:
                raise ValueError("Stored review item differs from actual snapshot: " + field)
        if item.get("review_state") == "completed":
            SemanticJudge._validate_completed(item, documents)
        by_id[item["source_qid"]] = item
    return by_id


def make_receipt(original, current, report, *, material_ready=False, calibration=None,
                 dispositions=None, grades=None):
    """Declare a provisional quality pool and blockers; no automatic publication.

    material_ready is a separate, attributed material review decision supplied by
    the caller. This function cannot infer business truth from issue counts.
    """
    validate_snapshot(original)
    reviewed = validate_review(current, report)
    if type(material_ready) is not bool:
        raise ValueError("Explicit material review disposition required")
    dispositions = dispositions or {}
    rows = []
    for q in original["questions"]:
        qid = q["qid"]
        item = reviewed.get(qid)
        reasons = []
        if item is None:
            disposition = dispositions.get(qid, "No current variant; disposition not supplied")
            status = "unresolved" if isinstance(disposition, dict) and disposition.get("action") == "unresolved" else "not_selected"
            reasons.append(disposition.get("reason", "No reason supplied") if isinstance(disposition, dict) else disposition)
        else:
            if item["review_state"] != "completed":
                reasons.append("Review incomplete; not evidence of a bad question")
            if item.get("item_validity") != "valid": reasons.append("Reader/critic has not established item validity")
            if item.get("reference_status") not in {"supported", "not_provided"}:
                reasons.append("Original reference proposal disputed or unresolved")
            findings = (item.get("adjudication") or {}).get("review_findings", {})
            if findings.get("substantive_defects"):
                reasons.append("Critic reports substantive package defects")
            status = "model_review_eligible" if not reasons else "unresolved"
        changed = current["identities"].get(qid) != original["identities"][qid]
        rows.append({"qid": qid, "status": status, "variant": "revised" if changed and item else "original",
            "reasons": reasons, "semantic_authority": "fallible_model_review"})
    extra = set(reviewed) - set(original["identities"])
    if extra:
        raise ValueError("Repair receipt cannot count new questions as repaired originals")
    grade_blockers = [r.get("qid") for r in grades or [] if r.get("quarantined")]
    calibration_ok = isinstance(calibration, dict) and calibration.get("status") == "passed"
    return {"version": VERSION, "original_snapshot_id": original["snapshot_id"],
        "current_snapshot_id": current["snapshot_id"], "result_scope": "research_only",
        "publication_effect": "none", "official_release": False,
        "original_count": len(rows), "rows": rows,
        "counts": {s: sum(row["status"] == s for row in rows)
                   for s in ("model_review_eligible", "unresolved", "not_selected")},
        "material_ready": material_ready, "calibration": deepcopy(calibration),
        "scoring_infrastructure_ready": calibration_ok,
        "scoring_ready": material_ready and calibration_ok and not grade_blockers
                         and bool(reviewed) and all(row["status"] == "model_review_eligible"
                                                    for row in rows if row["qid"] in reviewed),
        "quarantined_qids": sorted(set(grade_blockers)),
        "interpretation": "Eligibility is provisional model review, not measured true quality or a published yield."}


def grade_system_answers(frozen, review, predictions, *, judge_factory):
    """Grade one bound item across systems; any reference dispute freezes all its grades.

    Call again with a fresh review after reassessment. Prediction reuse is allowed
    only if its complete public input hash still matches. Old grades are never
    reused here, including after reference-only revisions.
    """
    validate_review(frozen, review)
    questions = {q["qid"]: q for q in frozen["questions"]}
    seen = set()
    for row in predictions:
        key = (row.get("system"), row.get("qid"))
        if not isinstance(key[0], str) or not key[0] or key in seen or key[1] not in questions:
            raise ValueError("Distinct system/qid predictions required")
        if row.get("prediction_input_hash") != frozen["identities"][key[1]]["prediction_input_hash"]:
            raise ValueError("Changed public input requires re-answering before grading")
        if "pred" not in row: raise ValueError("Missing prediction")
        seen.add(key)
    judge = judge_factory(frozen, review)
    rows, disputed = [], set()
    for row in predictions:
        grade = judge(questions[row["qid"]], deepcopy(row["pred"]))
        if grade.get("requires_item_reassessment"):
            disputed.add(row["qid"])
        rows.append({**deepcopy(row), **deepcopy(questions[row["qid"]]), "judgement": grade,
                     "snapshot_id": frozen["snapshot_id"], "result_scope": "research_only"})
    for row in rows:
        row["quarantined"] = row["qid"] in disputed
        row["correct"] = row["judgement"].get("correct")
        if row["quarantined"]:
            row["original_judgement"] = deepcopy(row["judgement"])
            row["judgement"] = {**row["judgement"], "verdict": "uncertain", "correct": None,
                "execution_status": "quarantined", "requires_item_reassessment": True,
                "reason": "A same-item reference/review dispute requires reassessment of every system."}
            row["judgement"]["item_reassessment"] = {
                **deepcopy(row["judgement"].get("item_reassessment") or {}),
                "required": True, "scope": "same_item_all_systems",
                "reasons": ["same_item_dispute"], "source_qid": row["qid"],
                "snapshot_id": frozen["snapshot_id"]}
            row["correct"] = None
    return {"version": VERSION, "snapshot_id": frozen["snapshot_id"], "rows": rows,
        "requires_item_reassessment": sorted(disputed), "result_scope": "research_only",
        "publication_effect": "none", "old_grades_reused": False}
