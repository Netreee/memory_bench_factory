"""Bounded public-context diagnostics, without grades or publication authority.

Each predeclared trial receives one natural answer in a fresh injected call.
The collection evidence envelope retains its source report for validation, but
only its compact observation is supplied to the interpreting collection reader.
"""
from copy import deepcopy
import json
from pathlib import Path

from eval.provenance import digest
from eval.public_context import build_full_context
from pipeline.quality_workflow import validate_snapshot
from pipeline.semantic_review import visible_documents

VERSION = "public-context-challenge/v1"
EVIDENCE_VERSION = "public-context-challenge-evidence/v1"
STEP = "context_challenge.answer"


def _copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _positive(value, name, *, zero=False):
    if type(value) is not int or value < (0 if zero else 1):
        raise ValueError(f"{name} must be an integer >= {0 if zero else 1}")


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def prepare_challenge(snapshot, trials, *, model, max_input_chars=500000,
                      max_tokens=16384, full_context_char_budget=120000,
                      solver_identity=None):
    """Validate all trial inputs before any call; never truncate a selected body.

    ``solver_identity`` is optional outer transport identity, not a model input.
    It must be supplied by the caller when a transport/profile binding is needed.
    Selected documents always keep their order in the original public corpus.
    """
    validate_snapshot(snapshot)
    if not _text(model):
        raise ValueError("An explicit model is required")
    for name, value in (("max_input_chars", max_input_chars), ("max_tokens", max_tokens),
                        ("full_context_char_budget", full_context_char_budget)):
        _positive(value, name)
    if solver_identity is not None and not isinstance(solver_identity, dict):
        raise ValueError("solver_identity must be a JSON object or None")
    if not isinstance(trials, list) or not trials:
        raise ValueError("A nonempty predeclared trial list is required")
    # Preserve the existing real solver prompt rather than a diagnostic variant.
    # Importing this read-only entry does not initialize provider configuration.
    from tools.run_agent_evaluation import ANSWER_SYSTEM, ANSWER_VERSION

    frozen_trials = _copy(trials)
    questions = {q["qid"]: q for q in snapshot["questions"]}
    documents, _ = visible_documents(snapshot["corpus"], include_titles=False)
    known = {d["doc_id"] for d in documents}
    settings = {"model": model, "max_input_chars": max_input_chars, "max_tokens": max_tokens,
                "full_context_char_budget": full_context_char_budget,
                "solver_identity": _copy(solver_identity)}
    params = {"model": model, "max_tokens": max_tokens, "temperature": 0.0,
              "top_p": 1.0, "retries": 1, "strict_json": True}
    source = {"version": VERSION, "snapshot_id": snapshot["snapshot_id"],
              "public_context_hash": snapshot["public_context_hash"],
              "documents_hash": digest(documents), "protocol_hash": digest(snapshot["protocol"]),
              "answer_version": ANSWER_VERSION, "answer_prompt_hash": digest(ANSWER_SYSTEM),
              "implementation_hash": digest(Path(__file__).read_text(encoding="utf-8")),
              "formatter_implementation_hash": digest((Path(__file__).resolve().parents[1] / "eval/public_context.py").read_text(encoding="utf-8")),
              "settings_hash": digest(settings), "planned_trials_hash": digest(frozen_trials)}
    rows, ids = [], set()
    for trial in frozen_trials:
        if (not isinstance(trial, dict) or set(trial) != {"id", "qid", "doc_ids", "reason"}
                or not _text(trial["id"]) or trial["id"] in ids
                or not _text(trial["qid"]) or trial["qid"] not in questions or not _text(trial["reason"])):
            raise ValueError("Each trial needs a unique id, existing qid, doc_ids and free-text reason")
        selected_ids = trial["doc_ids"]
        if (not isinstance(selected_ids, list) or not selected_ids
                or any(not isinstance(x, str) or x not in known for x in selected_ids)
                or len(set(selected_ids)) != len(selected_ids)):
            raise ValueError("Trial doc_ids must be nonempty, unique and present in the public corpus")
        ids.add(trial["id"])
        selected = [d for d in documents if d["doc_id"] in selected_ids]
        context, truncated, _ = build_full_context(
            [(d.get("session"), d.get("date"), d["content"]) for d in selected], budget=full_context_char_budget)
        if truncated:
            raise ValueError("Selected context exceeds full_context_char_budget; no truncation permitted")
        payload = {"public_question": questions[trial["qid"]]["question"],
                   "public_material": context, "public_protocol": snapshot["protocol"]}
        messages = [{"role": "system", "content": ANSWER_SYSTEM},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False, allow_nan=False)}]
        chars = sum(len(m["content"]) for m in messages)
        if chars > max_input_chars:
            raise ValueError("Trial exceeds max_input_chars; no truncation permitted")
        binding = {**source, "trial_hash": digest(trial), "qid": trial["qid"],
                   "question_hash": digest(payload["public_question"]),
                   "selected_documents_hash": digest(selected), "messages_hash": digest(messages)}
        rows.append({"id": trial["id"], "qid": trial["qid"],
            "doc_ids": [d["doc_id"] for d in selected], "requested_doc_ids": selected_ids,
            "reason": trial["reason"], "binding": binding, "messages": messages,
            "input_chars": chars, "body_chars": sum(len(d["content"]) for d in selected),
            "raw_output": None, "answer": None, "call_index": None,
            "execution": {"status": "not_executed"}})
    return {"version": VERSION, "mode": "prepared", "binding": source,
        "settings": settings, "params": params, "planned_trials": frozen_trials,
        "trials": rows, "calls_used": 0, "max_calls": 0, "records": [], "errors": [],
        "execution": {"status": "not_executed"}, "result_scope": "context_diagnostic_only",
        "grading_calls": 0, "publication_effect": "none", "formal_filter_eligible": False,
        "call_count_scope": "Injected single-attempt calls; actual physical attempts require outer transport trace audit.",
        "solver_identity_bound": solver_identity is not None,
        "purpose_authority": "Trial reasons are unverified design purposes, not expected answers or model evidence."}


def run_challenge(snapshot, trials, *, model, chat_json, max_calls,
                  max_input_chars=500000, max_tokens=16384,
                  full_context_char_budget=120000, solver_identity=None,
                  record=None, on_trial=None):
    """Return every slot and untouched raw output, including on callback failure.

    Negative or unknown natural answers are normal results. Provider, shape or
    audit errors stop all subsequent dispatch; there is no retry or refill.
    """
    _positive(max_calls, "max_calls", zero=True)
    if not callable(chat_json) or any(cb is not None and not callable(cb) for cb in (record, on_trial)):
        raise ValueError("chat_json and supplied callbacks must be callable")
    report = prepare_challenge(snapshot, trials, model=model, max_input_chars=max_input_chars,
        max_tokens=max_tokens, full_context_char_budget=full_context_char_budget,
        solver_identity=solver_identity)
    report.update(mode="executed", max_calls=max_calls, execution={"status": "running"})

    def fail(row, status, exc, phase):
        error = {"trial_id": row["id"], "phase": phase,
                 "error_type": type(exc).__name__, "message": str(exc)}
        report["errors"].append(error)
        row["execution"] = {"status": status, "error": deepcopy(error),
                            "prior_execution": deepcopy(row["execution"])}

    def emit(event):
        event = _copy(event)
        report["records"].append(event)
        if record is not None:
            record(deepcopy(event))

    for row in report["trials"]:
        if report["errors"]:
            row["execution"] = {"status": "skipped_after_failure"}
            continue
        if report["calls_used"] >= max_calls:
            row["execution"] = {"status": "call_budget_exhausted"}
            continue
        event = {"step": STEP, "trial_id": row["id"], "qid": row["qid"],
                 "binding": deepcopy(row["binding"]), "messages": deepcopy(row["messages"]),
                 "params": deepcopy(report["params"])}
        try:
            emit({**event, "event": "started", "output": None})
        except Exception as exc:
            fail(row, "audit_error", exc, "record_started")
            continue
        report["calls_used"] += 1
        row["call_index"] = report["calls_used"]
        row["execution"] = {"status": "dispatched"}
        try:
            raw = chat_json(STEP, deepcopy(row["messages"]), **deepcopy(report["params"]))
            try:
                row["raw_output"] = _copy(raw)
            except (ValueError, TypeError):
                row["raw_output"] = {"non_json_python_repr": repr(raw)}
                raise ValueError("Provider returned a non-JSON value")
            if isinstance(raw, dict) and "__error__" in raw:
                raise RuntimeError(str(raw["__error__"]))
        except Exception as exc:
            fail(row, "model_error", exc, "provider")
        else:
            if not isinstance(raw, dict) or not _text(raw.get("answer")):
                fail(row, "invalid_output", ValueError("A nonempty natural answer string is required"), "answer_shape")
            else:
                row["answer"] = raw["answer"]
                row["execution"] = {"status": "ok"}
        try:
            emit({**event, "event": "finished", "output": deepcopy(row["raw_output"]),
                  "execution": deepcopy(row["execution"]), "call_index": row["call_index"]})
        except Exception as exc:
            fail(row, "audit_error", exc, "record_finished")
            continue
        if on_trial is not None:
            try:
                on_trial(deepcopy(row))
            except Exception as exc:
                fail(row, "audit_error", exc, "on_trial")
    report["execution"] = {"status": "failed" if report["errors"] else
        "completed" if all(r["execution"]["status"] == "ok" for r in report["trials"]) else "incomplete"}
    return report


def _validate_report(snapshot, report):
    if not isinstance(report, dict) or report.get("version") != VERSION or report.get("mode") != "executed":
        raise ValueError("An executed context-challenge report is required")
    try:
        expected = prepare_challenge(snapshot, report["planned_trials"], **report["settings"])
        for key in ("binding", "settings", "params", "planned_trials", "result_scope",
                    "grading_calls", "publication_effect", "formal_filter_eligible",
                    "call_count_scope", "solver_identity_bound", "purpose_authority"):
            if report[key] != expected[key]:
                raise ValueError("Challenge report input binding mismatch: " + key)
        if len(report["trials"]) != len(expected["trials"]):
            raise ValueError("Challenge trial denominator changed")
        _positive(report["max_calls"], "max_calls", zero=True)
        _positive(report["calls_used"], "calls_used", zero=True)
        called, stopped = [], False
        allowed = {"ok", "model_error", "invalid_output", "audit_error",
                   "call_budget_exhausted", "skipped_after_failure"}
        for row, original in zip(report["trials"], expected["trials"]):
            for key in original.keys() - {"raw_output", "answer", "call_index", "execution"}:
                if row[key] != original[key]:
                    raise ValueError("Challenge trial identity/message mismatch: " + key)
            status = row["execution"]["status"]
            if status not in allowed:
                raise ValueError("Incomplete or unknown trial status")
            if stopped and (row["call_index"] is not None or status != "skipped_after_failure"):
                raise ValueError("A failure was followed by another trial dispatch")
            if row["call_index"] is not None:
                if type(row["call_index"]) is not int:
                    raise ValueError("Invalid call index")
                called.append(row["call_index"])
            if row["answer"] is not None and (
                    row["call_index"] is None or not isinstance(row["raw_output"], dict)
                    or not _text(row["answer"]) or row["raw_output"].get("answer") != row["answer"]):
                raise ValueError("Answer does not match actual raw output")
            if status == "ok" and row["answer"] is None:
                raise ValueError("Completed answer is missing")
            if status in {"call_budget_exhausted", "skipped_after_failure"} and (
                    row["call_index"] is not None or row["raw_output"] is not None or row["answer"] is not None):
                raise ValueError("Unexecuted slot contains an answer")
            stopped = stopped or status in {"model_error", "invalid_output", "audit_error"}
        if called != list(range(1, report["calls_used"] + 1)) or report["calls_used"] > report["max_calls"]:
            raise ValueError("Call budget or index mismatch")
        statuses = [r["execution"]["status"] for r in report["trials"]]
        failed = any(s in {"model_error", "invalid_output", "audit_error"} for s in statuses)
        wanted = "failed" if failed else "completed" if all(s == "ok" for s in statuses) else "incomplete"
        if report["execution"]["status"] != wanted or bool(report["errors"]) != failed:
            raise ValueError("Report execution summary differs from slots")
        # A source report is an audit artifact, never proof that a provider was honest.
        # Still require its internal recorded messages and returns to agree.
        known_ids = {r["id"] for r in report["trials"]}
        if any(r.get("trial_id") not in known_ids for r in report["records"]):
            raise ValueError("An unreserved trial record was supplied")
        for row in report["trials"]:
            records = [r for r in report["records"] if r.get("trial_id") == row["id"]]
            if any(r.get("step") != STEP or r.get("messages") != row["messages"]
                   or r.get("binding") != row["binding"] or r.get("params") != report["params"] for r in records):
                raise ValueError("Recorded trial input differs")
            if row["call_index"] is not None:
                # Callback failures wrap the already-recorded result. They do
                # not relax the binding of its answer or original raw output.
                recorded_execution = row["execution"]
                if recorded_execution["status"] == "audit_error":
                    recorded_execution = recorded_execution["prior_execution"]
                if ([r.get("event") for r in records] != ["started", "finished"]
                        or records[-1].get("output") != row["raw_output"]
                        or records[-1].get("call_index") != row["call_index"]
                        or records[-1].get("execution") != recorded_execution):
                    raise ValueError("Dispatched trial lacks matching recorded output")
                if recorded_execution["status"] == "ok" and row["answer"] is None:
                    raise ValueError("Recorded successful answer is missing")
            elif row["execution"]["status"] == "audit_error":
                if ([r.get("event") for r in records] != ["started"]
                        or row["raw_output"] is not None or row["answer"] is not None):
                    raise ValueError("Undispatched audit failure contains a returned output")
            elif records:
                raise ValueError("Unexecuted slot contains dispatch records")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Unreadable context-challenge report") from exc
    return report


def _observation(report):
    return {"version": EVIDENCE_VERSION, "source_report_hash": digest(report),
        "source_snapshot_id": report["binding"]["snapshot_id"],
        "settings": deepcopy(report["settings"]), "execution": deepcopy(report["execution"]),
        "calls_used": report["calls_used"], "errors": deepcopy(report["errors"]),
        "trials": [{key: deepcopy(row[key]) for key in ("id", "qid", "doc_ids", "reason", "binding",
                    "raw_output", "answer", "execution", "call_index")} for row in report["trials"]],
        "scope": "Actual answers under exactly the listed original public documents and unchanged public protocol. "
                 "Trial reasons are unverified purposes. No matched full-context baseline is supplied, no grade or difficulty is established. "
                 "Execution errors and legitimate unknown answers are distinct. Missing answers are not wrong answers."}


def prepare_collection_evidence(snapshot, report):
    """Bind a complete source report and expose only a compact observation to LLMs."""
    source = _copy(report)
    _validate_report(snapshot, source)
    return {"version": EVIDENCE_VERSION, "source_report": source,
            "observation": _observation(source)}


def validate_collection_evidence(snapshot, evidence):
    """Return the validated compact observation; never send source audit logs twice."""
    if (not isinstance(evidence, dict) or set(evidence) != {"version", "source_report", "observation"}
            or evidence["version"] != EVIDENCE_VERSION):
        raise ValueError("Invalid collection evidence envelope")
    _validate_report(snapshot, evidence["source_report"])
    expected = _observation(evidence["source_report"])
    if evidence["observation"] != expected:
        raise ValueError("Collection observation differs from its source report")
    return deepcopy(expected)
