"""Public semantic review inside the original questions/corpus merge stage.

This adapts the existing readers; it never creates a world, question or gold.
Legacy lexical grounding is retained as a diagnostic, not a semantic veto.
"""
from collections import Counter
from copy import deepcopy

from pipeline.grounding import gather_evidence, _sessions_of, run_grounding
from pipeline.semantic_review import review_questions, review_certification

VERSION = "original-grounding-semantic/v2"
REVIEW_ARTIFACT = "06_semantic_review.json"


def enabled(wp):
    return (wp.get("quality_contract") or {}).get("public_semantic_review") is True


def execution_complete(review):
    """Require actual complete stage outputs, independently of semantic verdicts."""
    if not isinstance(review, dict) or review.get("audit_error"):
        return False
    items = review.get("items")
    if not isinstance(items, list) or not items:
        return False
    for item in items:
        if not isinstance(item, dict):
            return False
        stages = item.get("stage_execution")
        required = {"blind_read", "adjudicate"}
        if (item.get("binding") or review.get("binding") or {}).get("reference_auditor_model"):
            required.add("reference_audit")
        if not isinstance(stages, dict) or not required <= stages.keys():
            return False
        if any(not isinstance(value, dict) or value.get("status") != "ok" for value in stages.values()):
            return False
    return True


def candidates_with_evidence(questions, corpus, *, isolated_reference=False):
    # This semantic path reads the complete public history. A later document
    # may retrospectively support an earlier fact; canonical evidence_sessions
    # therefore cannot delimit its candidate pool. This is not a proof set.
    pool = list(dict.fromkeys(str(d["doc_id"])
                for session in _sessions_of(corpus) for d in session.get("docs", [])
                if d.get("is_filler") is not True and d.get("doc_id")))
    rows = []
    from eval.grading import requires_semantic_grading
    from pipeline.reference_audit import validate_reference_proposal
    for question in questions:
        if requires_semantic_grading(question):
            # Never manufacture a natural reference from the canonical witness.
            validate_reference_proposal(question.get("reference_proposal"))
        rows.append({**deepcopy(question), "evidence_doc_ids": pool[:],
                     "candidate_evidence_doc_ids": pool[:],
                     "evidence_scope": {"kind": "candidate_pool", "minimal_proof": "not_measured",
                                        "necessary_document_count": None, "difficulty_verified": False}})
        if isolated_reference and "reference_proposal" not in rows[-1]:
            # Expose the original reference as an explicit audit target, never
            # substitute the blind reader's answer. Keep the raw gt unchanged.
            answer = deepcopy(question.get("gt"))
            encoding = None
            if (question.get("capability") in {"IE", "MR"}
                    and isinstance(answer, dict) and "value" in answer):
                # These original capabilities have always graded gt.value;
                # at_week is a zero-based internal locator, not an extra answer.
                # Preserve gt verbatim on the row and record the source pointer.
                answer = deepcopy(answer["value"])
                encoding = {"raw_value": deepcopy(question["gt"]), "answer_pointer": "/value",
                            "scope": "Original value-answer wire projection; metadata is not public evidence"}
            if answer == "INSUFFICIENT_EVIDENCE":
                subtype = (question.get("question_contract") or {}).get("abstention_kind")
                answer = {"never_known": "无此项/查无此记录", "forgotten": "已停止统计/不再跟踪",
                          "out_of_scope": "信息不足/不在记录范围内"}.get(subtype, "信息不足，无法确定")
                encoding = {"raw_value": question["gt"], "declared_subtype": subtype,
                            "scope": "Reference encoding only, not public factual evidence"}
            rows[-1]["reference_proposal"] = {"answer": answer, "rationale": ""}
            rows[-1]["reference_projection"] = {"version": "original-gt-audit-target/v2",
                                                "source": "gt", "encoding": encoding}
    return rows


def validate_delivery(questions, corpus, protocol, review):
    """Certify the selected subset's procedure; keep failed candidates pending.

    Only completed parsed-format failures may be isolated. Provider, budget,
    input-limit, audit-sink and missing-stage failures retain the whole-run stop.
    This never edits an opinion, repairs a reference or judges business truth.
    """
    result = {"version": "grounding-candidate-isolation/v1", "delivery_safe": False,
              "execution_complete": execution_complete(review), "source_count": len(questions),
              "selected_count": 0, "pending_count": 0, "parsed_format_failures": [], "issues": []}
    try:
        if not isinstance(review, dict) or review.get("audit_error"):
            raise ValueError("Missing review or global audit failure")
        accounting = review.get("execution_accounting") or {}
        if accounting.get("execution_stopped") or accounting.get("suppressed_after_failure", 0):
            raise ValueError("Review provider execution stopped")
        if not questions or len({q.get("qid") for q in questions}) != len(questions):
            raise ValueError("Source candidates must be nonempty with unique identities")
        # Existing evaluator replay covers full input, current references and
        # every completed item's exact outputs/locators/certification.
        validate_current_review(questions, corpus, protocol, review)
        kept, routing = selection(questions, review)
        result.update(selected_count=len(kept), pending_count=routing["n_pending"],
                      rejected_count=routing["n_dropped"])
        items = review["items"]
        if len({item.get("candidate_id") for item in items}) != len(items):
            raise ValueError("Duplicate candidate trace identities")
        for item in items:
            stages = item.get("stage_execution")
            required = {"blind_read", "adjudicate"}
            if (item.get("binding") or review.get("binding") or {}).get("reference_auditor_model"):
                required.add("reference_audit")
            if not isinstance(stages, dict) or set(stages) != required:
                raise ValueError("Missing or unknown review stages")
            for stage, execution in stages.items():
                status = execution.get("status") if isinstance(execution, dict) else None
                if status == "ok":
                    continue
                allowed = "invalid_output" if stage == "reference_audit" else "invalid_review"
                if status != allowed:
                    raise ValueError(f"Non-isolatable execution failure: {stage}:{status}")
                raw = ((item.get("reference_audit") or {}).get("raw_output") if stage == "reference_audit"
                       else item.get(stage + "_raw_output"))
                if (not isinstance(raw, (dict, list))
                        or isinstance(raw, dict) and "__error__" in raw):
                    raise ValueError("Format failure lacks an actual parsed raw opinion")
                if item.get("review_state") != "pending" or review_certification(item)["status"] == "certified":
                    raise ValueError("Incomplete candidate must remain pending and uncertified")
                result["parsed_format_failures"].append({"qid": item["source_qid"], "stage": stage,
                                                        "execution": deepcopy(execution)})
        if result["parsed_format_failures"]:
            # A partial delivery must retain the complete original attempt log,
            # including the malformed replies, rather than drop failed rows.
            records = review.get("records")
            if not isinstance(records, list):
                raise ValueError("Partial review lacks its original trace records")
            for item in items:
                for stage, execution in item["stage_execution"].items():
                    events = [r for r in records if isinstance(r, dict)
                              and r.get("candidate_id") == item["candidate_id"] and r.get("stage") == stage]
                    starts = [r for r in events if r.get("event") == "started"]
                    ends = [r for r in events if r.get("event") == "finished"]
                    if len(events) != 2 or len(starts) != 1 or len(ends) != 1:
                        raise ValueError("Partial review lacks unique started/finished stage records")
                    start, end = starts[0], ends[0]
                    raw = ((item.get("reference_audit") or {}).get("raw_output") if stage == "reference_audit"
                           else item.get(stage + "_raw_output"))
                    if (end.get("output") != raw or end.get("execution") != execution
                            or any(start.get(k) != end.get(k) for k in ("messages", "params", "binding"))
                            or end.get("binding") != item.get("binding")):
                        raise ValueError("Partial review trace differs from its original stage opinion")
        if not kept:
            raise ValueError("No completely certified candidate is available for delivery")
        validate_current_review(kept, corpus, protocol, review)
        result["delivery_safe"] = True
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        result["issues"].append({"code": "semantic_review_execution_not_delivery_safe",
                                 "message": f"{type(exc).__name__}: {exc}"})
    return result


def selection(questions, review):
    """Route recorded LLM decisions, without interpreting free-text reasons."""
    if len(questions) != len(review.get("items", [])):
        raise ValueError("Review must retain every original candidate")
    kept, pending, rejected = [], [], []
    for question, item in zip(questions, review["items"]):
        if item.get("source_qid") != question.get("qid"):
            raise ValueError("Review candidate order/identity mismatch")
        ready = (item.get("review_state") == "completed"
                 and review_certification(item)["status"] == "certified")
        verdict = {k: item.get(k) for k in ("item_validity", "answerability", "reference_status")}
        row = {"qid": question.get("qid"), "line": question.get("line"),
               "capability": question.get("capability"), **verdict,
               "reason": (item.get("adjudication") or {}).get("reasoning"),
               "concerns": (item.get("adjudication") or {}).get("concerns", [])}
        if not ready or any(v in ("ambiguous", "unresolved", None) for v in verdict.values()):
            pending.append(row)
        elif (verdict["item_validity"] == "valid" and verdict["reference_status"] == "supported"
              and verdict["answerability"] in ("answerable", "unanswerable")):
            kept.append(question)
        else:
            rejected.append(row)
    def counts(field):
        total = Counter(q.get(field, "?") for q in questions)
        final = Counter(q.get(field, "?") for q in kept)
        return {k: {"n": n, "grounded": final[k], "survival": round(final[k] / n, 3)}
                for k, n in sorted(total.items())}
    return kept, {"version": VERSION,
                  "overall": {"n": len(questions), "grounded": len(kept),
                              "survival": round(len(kept) / len(questions), 3) if questions else None},
                  "by_line": counts("line"), "by_capability": counts("capability"),
                  "n_dropped": len(rejected), "n_pending": len(pending),
                  "drops": rejected, "pending": pending,
                  "decision_basis": "fallible_public_semantic_review",
                  "limitations": ["Model decisions are not verified truth.",
                                  "Candidate evidence is not necessary evidence or measured difficulty."]}


def review_grounding(questions, corpus, protocol, *, chat_json, model,
                     max_calls=None, max_tokens=4096, max_input_chars=200000, record=None,
                     isolated_reference=False):
    candidates = candidates_with_evidence(questions, corpus, isolated_reference=isolated_reference)
    _, lexical = run_grounding(questions, corpus)
    stopped = False
    caller_invocations = 0
    suppressed_after_failure = 0

    def invoke(step, messages, **kwargs):
        nonlocal stopped, caller_invocations, suppressed_after_failure
        if stopped:
            suppressed_after_failure += 1
            raise RuntimeError("Previous review execution failed; no further provider calls")
        try:
            caller_invocations += 1
            result = chat_json(step, messages, **kwargs)
            if isinstance(result, dict) and "__error__" in result:
                raise RuntimeError(str(result["__error__"]))
            return result
        except Exception:
            stopped = True
            raise

    review = review_questions(candidates, corpus, protocol, chat_json=invoke,
                              reviewer_model=model, reader_model=model, max_calls=max_calls,
                              max_tokens=max_tokens, max_input_chars=max_input_chars, record=record,
                              reference_auditor_model=model if isolated_reference else None)
    kept, report = selection(candidates, review)
    report["lexical_diagnostic"] = lexical
    report["execution_stopped"] = stopped
    report["execution_complete"] = execution_complete(review)
    accounting = {"execution_stopped": stopped, "caller_invocations": caller_invocations,
                  "suppressed_after_failure": suppressed_after_failure,
                  "logical_calls_used": review["calls_used"],
                  "paid_provider_calls": None,
                  "scope": "Forwarded callable invocations; actual provider attempts and charges require the provider trace/bill."}
    report.update(accounting)
    review["execution_accounting"] = deepcopy(accounting)
    delivery = validate_delivery(candidates, corpus, protocol, review)
    report["delivery_safe"] = delivery["delivery_safe"]
    report["delivery_validation"] = delivery
    return kept, report, review


def validate_current_review(questions, corpus, protocol, review):
    """Replay the same version and receipt checks used by the original evaluator.

    No grading is performed and the caller has a zero API allowance.
    Full-review receipts can serve a selected subset without changing identity.
    """
    from eval.semantic_judge import SemanticJudge
    from eval.provenance import protocol_scoring_policy
    scoring_policy = protocol_scoring_policy(protocol)
    def no_call(*args, **kwargs):
        raise AssertionError("Release validation cannot call a provider")
    SemanticJudge(review, questions, corpus, protocol,
                  model=review["binding"]["reviewer_model"], chat_json=no_call, max_calls=0,
                  scoring_policy=scoring_policy)
