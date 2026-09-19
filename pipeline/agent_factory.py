"""One bounded, injected author/reader/editor research case.

The plan chooses every model and call limit. Provider attempts, not optimistic
submodule counters, enforce the shared budget. Material opinions never become
Python-certified truth and this entry point never publishes or grades systems.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from threading import RLock

from eval.provenance import digest
from pipeline import agent_editing, material_series, question_series, quality_workflow


VERSION = "agent-case-factory/v1"
TEAM_VERSION = "agent-case-factory/v2"
PHASES = ("material_generation", "material_review", "material_edit", "material_rereview",
          "question_generation", "question_review", "question_edit", "final_review")
TEAM_PHASES = ("material_generation", "material_team", "question_generation",
               "question_review", "collection_review", "question_edit", "final_review",
               "final_collection_review")
REVIEW_PHASES = {"question_review", "final_review"}
QUESTION_CYCLE_PHASES = ("question_review", "collection_review", "question_edit",
                         "final_review", "final_collection_review")
QUESTION_CYCLE_VERSION = "agent-question-cycle/v1"
REVIEW_REUSE_POLICY = "same-input-within-run/v1"
_REUSE_SOURCES = {
    "pipeline/agent_factory.py", "pipeline/agent_editing.py", "pipeline/quality_workflow.py",
    "pipeline/semantic_review.py", "pipeline/question_set_review.py", "pipeline/reference_locations.py",
    "eval/semantic_judge.py", "eval/answer_task_review.py", "eval/grading.py", "eval/provenance.py",
    "tools/run_bc_case.py", "config.py", "llm_trace.py", "llm_transport.py"}


def case_phases(plan):
    return TEAM_PHASES if plan.get("material_mode") == "team/v1" else PHASES


def _copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


class AgentCaseError(RuntimeError):
    """An orchestration/audit failure with the complete available case attached."""
    def __init__(self, message, report):
        super().__init__(message)
        self.report = deepcopy(report)


def _plan(plan):
    value = _copy(plan)
    base_keys = {"max_provider_attempts", "material_batches", "question_batches", "phases"}
    if (not isinstance(value, dict) or not base_keys <= set(value)
            or set(value) - base_keys - {"material_mode", "review_reuse"}):
        raise ValueError("plan needs max_provider_attempts, material_batches, question_batches and phases")
    if "material_mode" in value and value["material_mode"] != "team/v1":
        raise ValueError("The explicit material_mode must be team/v1; omit it for the legacy plan")
    _validate_reuse_policy(value, team_mode=value.get("material_mode") == "team/v1")
    if type(value["max_provider_attempts"]) is not int or value["max_provider_attempts"] < 0:
        raise ValueError("max_provider_attempts must be a nonnegative integer")
    if not isinstance(value["phases"], dict) or set(value["phases"]) != set(case_phases(value)):
        raise ValueError("Every phase needs an explicit budget and model")
    material_series._validate_specs(value["material_batches"])
    question_series._batch_specs(value["question_batches"])
    _validate_phase_specs(value["phases"])
    return value


def _validate_reuse_policy(plan, *, team_mode):
    if "review_reuse" in plan and (not team_mode or plan["review_reuse"] != REVIEW_REUSE_POLICY):
        raise ValueError("Explicit review_reuse requires team mode and same-input-within-run/v1")


def _reuse_stamp(plan, context):
    """A frozen runner's execution conditions, never evidence of semantic truth."""
    if not isinstance(context, dict) or set(context) != {"transport", "source_sha256"}:
        return None, "missing_frozen_execution_context"
    sources = context["source_sha256"]
    required = set(_REUSE_SOURCES)
    if any(s.get("reference_auditor_model") for s in plan["phases"].values()):
        required.add("pipeline/reference_audit.py")
    if not isinstance(sources, dict) or not required <= set(sources) or context["transport"] is None:
        return None, "incomplete_frozen_execution_context"
    root = Path(__file__).resolve().parents[1]
    try:
        for name, expected in sources.items():
            if not isinstance(name, str) or not isinstance(expected, str):
                return None, "invalid_frozen_source_identity"
            path = (root / name).resolve()
            if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                return None, "frozen_implementation_changed"
        # Validate this opt-in dependency only after checking its frozen bytes.
        from llm_transport import validate_transport, resolve_profile, transport_fingerprint
        transport = validate_transport(context["transport"])
        for name in ("question_review", "final_review", "collection_review", "final_collection_review"):
            spec = plan["phases"][name]
            models = ([spec[k] for k in ("reader_model", "reviewer_model", "reference_auditor_model") if k in spec]
                      if name in REVIEW_PHASES else [spec["model"]])
            if any(resolve_profile(transport, model)["profile"] is None for model in models):
                return None, "legacy_transport_cannot_establish_reuse_conditions"
        return {"transport": transport, "transport_fingerprint": transport_fingerprint(transport),
                "source_sha256": deepcopy(sources)}, None
    except (OSError, ValueError, TypeError, KeyError):
        return None, "invalid_or_changed_frozen_execution_context"


def _reuse_report(report, source_phase, target_phase, initial, original, current, *,
                  initial_stamp, context, collection=False):
    """Reuse one whole completed report; never select favorable items or opinions."""
    note = {"policy": REVIEW_REUSE_POLICY, "mode": "fresh", "source_phase": source_phase,
            "target_phase": target_phase, "scope": "same invocation, whole batch, exact inputs only"}
    report.setdefault("within_run_review_reuse", {})[target_phase] = note
    reason = None
    if any(original[k] != current[k] for k in ("questions", "corpus", "protocol")):
        reason = "batch_inputs_changed"
    elif report["plan"]["phases"][source_phase] != report["plan"]["phases"][target_phase]:
        reason = "phase_conditions_differ"
    else:
        stamp, reason = _reuse_stamp(report["plan"], context)
        if reason is None and initial_stamp is None:
            reason = "initial_execution_conditions_unverified"
        elif reason is None and stamp != initial_stamp:
            reason = "execution_conditions_changed"
    if reason is None:
        try:
            if collection:
                if initial.get("execution", {}).get("status") != "ok" or initial.get("proposal_ready") is not True:
                    raise ValueError("Collection review was not completed")
                from pipeline.question_set_review import review_question_set
                spec = report["plan"]["phases"][source_phase]
                def stored_output(step, messages, **params):
                    if messages != initial["messages"]:
                        raise ValueError("Collection review messages differ")
                    return deepcopy(initial["raw_output"])
                # Local validation of the saved raw object through the existing
                # validator; this callback cannot dispatch a provider request.
                checked = review_question_set(current["questions"], current["corpus"], current["protocol"],
                    **spec, chat_json=stored_output)
                fields = ("binding", "messages", "inputs", "raw_output", "proposal", "proposal_ready",
                          "execution", "format_issues", "issues", "resolved_evidence",
                          "issue_evidence_locations", "output_hash", "collection_review_version")
                if any(checked.get(k) != initial.get(k) for k in fields):
                    raise ValueError("Collection review cannot be revalidated")
            else:
                items = quality_workflow.validate_review(current, initial)
                if (initial.get("execution", {}).get("status") not in (None, "ok")
                        or initial.get("audit_failures") or not items
                        or any(i.get("review_state") != "completed" or i.get("execution", {}).get("status") != "ok"
                               or i.get("item_certification", {}).get("status") != "certified" for i in items.values())):
                    raise ValueError("Item review was not completed")
        except (ValueError, TypeError, KeyError, AttributeError):
            reason = "initial_review_incomplete_or_binding_invalid"
    if reason is not None:
        note["reason"] = reason
        return None
    note.update(mode="reused", reused_from={"phase": source_phase,
        "report_hash": digest(initial), "snapshot_id": original["snapshot_id"]},
        new_provider_calls=0, historical_calls_used=initial.get("calls_used", 0),
        execution_conditions=deepcopy(initial_stamp),
        semantic_authority="unchanged fallible model opinion; not a new review or correctness proof",
        sampling_tradeoff="Reuses the earlier sample instead of obtaining an independent new sample; semantic losslessness is not established.")
    reused = deepcopy(initial)
    reused["within_run_reuse"] = deepcopy(note)
    return reused



def _validate_phase_specs(phases):
    for name, spec in phases.items():
        keys = {"max_calls", "max_input_chars", "max_tokens"}
        keys |= ({"models"} if name == "material_team" else
                 {"reader_model", "reviewer_model", "workers"} if name in REVIEW_PHASES else {"model"})
        permitted = (keys, keys | {"reference_auditor_model"}) if name in REVIEW_PHASES else (keys,)
        if not isinstance(spec, dict) or set(spec) not in permitted:
            raise ValueError(f"Unexpected or missing phase settings: {name}")
        for key in ("max_calls", "max_input_chars", "max_tokens"):
            if type(spec[key]) is not int or spec[key] < (0 if key == "max_calls" else 1):
                raise ValueError(f"Invalid {name}.{key}")
        if name == "material_team":
            from pipeline.material_team import PHASES as material_team_phases
            if (not isinstance(spec["models"], dict) or set(spec["models"]) != set(material_team_phases)
                    or any(not isinstance(m, str) or not m.strip() for m in spec["models"].values())
                    or spec["max_calls"] > 6):
                raise ValueError("material_team requires six explicit role models and at most six attempts")
        for key in (() if name == "material_team" else
                    ("reader_model", "reviewer_model") if name in REVIEW_PHASES else ("model",)):
            if not isinstance(spec[key], str) or not spec[key].strip():
                raise ValueError(f"Explicit {name}.{key} required")
        if "reference_auditor_model" in spec and (not isinstance(spec["reference_auditor_model"], str)
                or not spec["reference_auditor_model"].strip()):
            raise ValueError("Explicit nonempty reference_auditor_model required when selected")
        if name in REVIEW_PHASES and (type(spec["workers"]) is not int or not 1 <= spec["workers"] <= 4):
            raise ValueError("Review workers must be between one and four")
        if name in {"material_review", "material_edit", "material_rereview", "collection_review",
                    "final_collection_review", "question_edit"} and spec["max_calls"] > 1:
            raise ValueError(f"{name} allows at most one attempt")

def _editor_evidence_projection(evidence, documents):
    """Omit only verified copies of public fields in program-owned locator slots."""
    from pipeline.semantic_review import fingerprint
    projected = _copy(evidence)
    if (not isinstance(projected, list) or not isinstance(documents, list)
            or any(not isinstance(doc, dict) or not isinstance(doc.get("doc_id"), str)
                   for doc in documents)):
        return projected
    by_id = {doc["doc_id"]: doc for doc in documents}
    if len(by_id) != len(documents):
        return projected
    for row in projected:
        if not isinstance(row, dict) or "resolved_text_source" in row:
            continue
        doc_id, field = row.get("doc_id"), row.get("field")
        if not isinstance(doc_id, str) or not isinstance(field, str):
            continue
        doc = by_id.get(doc_id)
        if (doc is None or field not in {"content", "title", "date", "session"}
                or not isinstance(doc.get(field), str)
                or row.get("locator_source") != "program_resolved_field"
                or row.get("location_scope") != "field" or "quote" in row
                or row.get("resolved_text") != doc[field]
                or row.get("document_hash") != fingerprint(doc)
                or row.get("field_hash") != fingerprint(doc[field])):
            continue
        # doc_id, field and both hashes remain beside this reversible marker.
        # Opinion text, quotes and failed/ambiguous reference targets stay intact.
        del row["resolved_text"]
        row["resolved_text_source"] = "documents"
    return projected


_EDITOR_EVIDENCE_NOTE = (
    "程序定位中 resolved_text_source=documents 表示省略了与本次公开 documents 完全相同的重复正文；"
    "请按该项 doc_id、field 及保留的哈希回查全文。定位成功不代表意见成立。")


def _editor_issues(review, collection=None, *, documents=None):
    """Carry complete fallible readings, not a predefined list of failed labels."""
    issues = []
    for index, item in enumerate(review["items"]):
        observations = {key: _copy(item.get(key)) for key in (
            "blind_read", "adjudication", "execution", "stage_execution",
            "stage_evidence_location", "item_certification")}
        locations = observations["stage_evidence_location"]
        if isinstance(locations, dict):
            for stage in ("blind_read", "reference_audit", "adjudicate"):
                location = locations.get(stage)
                if not isinstance(location, dict):
                    continue
                if "resolved_evidence" in location:
                    location["resolved_evidence"] = _editor_evidence_projection(
                        location["resolved_evidence"], documents)
                if isinstance(location.get("entries"), list):
                    for entry in location["entries"]:
                        if isinstance(entry, dict) and entry.get("status") == "located" and "resolved" in entry:
                            entry["resolved"] = _editor_evidence_projection([entry["resolved"]], documents)[0]
        compacted = locations != item.get("stage_evidence_location")
        if item.get("reference_audit") is not None:
            # Preserve all opinion text and errors without repeating the full
            # corpus already supplied to the editor inside audit request logs.
            observations["reference_audit"] = {key: _copy(item["reference_audit"].get(key)) for key in (
                "raw_output", "proposal_ready", "execution", "format_issues", "reference_audit_version",
                "reference_location_policy")}
            observations["reference_audit"]["claim_locations"] = [
                {key: _copy(value) for key, value in claim.items() if key != "resolved_evidence"}
                for claim in item["reference_audit"].get("claim_locations", [])]
        # A malformed return may have usable discussion but is not promoted into
        # item.blind_read/adjudication by the review API. Keep it visibly raw.
        observations["unvalidated_stage_returns"] = [
            {key: _copy(event.get(key)) for key in ("stage", "output", "execution")}
            for event in review.get("records", [])
            if event.get("source_qid") == item["source_qid"] and event.get("event") == "finished"
            and item.get({"blind_read": "blind_read", "reference_audit": "reference_audit"}.get(
                event.get("stage"), "adjudication")) is None]
        issues.append({"issue_id": f"review-{index + 1:06d}", "qid": item["source_qid"],
            "description": (
                "以下是本题此前完整盲读、参考审阅及执行记录，均可被推翻，不是标准答案或必须修成的目标。"
                "先阅读本次题面与全部公开正文，再核查实质问题、合理歧义、可接受省略和理由出处。"
                "即使旧审阅认为成立也要核对；若旧审阅未完成，不得假定题目有错。"
                "参考有错只改参考，题面确有必要才改，允许有据拒绝、保留、弃题或未决。"
                + (_EDITOR_EVIDENCE_NOTE if compacted else "") + "\n"
                + json.dumps(observations, ensure_ascii=False)),
            "evidence": [], "severity": "此前审阅意见，影响程度须依据完整语义重新判断。"})
    if collection is not None:
        observations = {key: _copy(collection.get(key)) for key in (
            "proposal", "raw_output", "execution", "format_issues", "resolved_evidence")}
        observations["resolved_evidence"] = _editor_evidence_projection(observations["resolved_evidence"], documents)
        compacted = observations["resolved_evidence"] != collection.get("resolved_evidence")
        issues.append({"issue_id": "collection-review", "description": (
            "以下是整批题目的完整可质疑意见，含重合组、覆盖观察与限制；不是删除名单或标准答案。"
            "请结合完整题意与公开正文判断，允许反驳或保留合理简单题。"
            "覆盖、单题正确性、难度分别说明，不得把未运行的系统表现当作事实。"
            + (_EDITOR_EVIDENCE_NOTE if compacted else "") + "\n"
            + json.dumps(observations, ensure_ascii=False)),
            "evidence": [], "severity": "整批覆盖与重复建议；不自动意味着单题错误。"})
    return issues



def _orchestration(report, chat_json, record, on_phase, *, strict=False, before_call=None):
    frozen_plan = report["plan"]
    lock = RLock()
    audit_failure = []
    fatal = []

    def latch(name, exc, *, force=False):
        if (strict or force) and not fatal:
            fatal.append({"phase": name, "error_type": type(exc).__name__, "message": str(exc)})
            report["fatal_failures"] = deepcopy(fatal)

    def emit(name, event):
        with lock:
            entry = {**_copy(event), "phase": name}
            report["records"].append(entry)
            if audit_failure:
                raise RuntimeError("An earlier audit callback failed")
            if record:
                try:
                    record(deepcopy(entry))
                except Exception as exc:
                    audit_failure.append({"phase": name, "error_type": type(exc).__name__, "message": str(exc)})
                    latch(name, exc)
                    raise
            # An invalid wire/schema return cannot be healed by a later role.
            # Semantic disagreement and incomplete editorial feedback are not
            # transport failures and keep their existing diagnostic behavior.
            if strict and entry.get("execution", {}).get("status") in {
                    "model_error", "invalid_output", "invalid_review", "input_limit", "audit_error"}:
                exc = RuntimeError("Question cycle transport/audit failed: " + entry["execution"]["status"])
                latch(name, exc)
                raise exc

    def provider(name):
        def call(step, messages, **params):
            attempt = None
            provider_started = False
            try:
                with lock:
                    if params.get("retries") != 1:
                        raise ValueError("The factory only permits one provider attempt per injected call")
                    if audit_failure or fatal:
                        raise RuntimeError("An earlier audit/transport failure stopped dispatch")
                    budget = report["budget"]
                    if (budget["actual_provider_attempts"] >= budget["max_provider_attempts"]
                            or budget["phase_provider_attempts"][name] >= frozen_plan["phases"][name]["max_calls"]):
                        raise RuntimeError("Actual provider-attempt budget exhausted")
                    call_messages, call_params = deepcopy(messages), deepcopy(params)
                    attempt = {"attempt": budget["actual_provider_attempts"] + 1, "phase": name, "step": step,
                        "messages": _copy(messages), "params": _copy(params), "status": "started", "raw_output": None}
                    if before_call:
                        before_call(deepcopy(attempt))
                    budget["actual_provider_attempts"] += 1
                    budget["phase_provider_attempts"][name] += 1
                    report["provider_attempts"].append(attempt)
                # Reserving under the lock is the dispatch boundary. Calls
                # already in flight may finish after another worker fails.
                provider_started = True
                raw = chat_json(step, call_messages, **call_params)
            except Exception as exc:
                with lock:
                    # Only an exception from the injected provider forces the
                    # non-strict case to stop; local budget/input limits keep
                    # their existing diagnostic behavior.
                    latch(name, exc, force=provider_started)
                    if attempt is not None:
                        attempt.update(status="provider_error", error_type=type(exc).__name__, message=str(exc))
                raise
            with lock:
                try:
                    attempt["raw_output"] = _copy(raw)
                except (TypeError, ValueError):
                    attempt["raw_output"] = {"non_json_python_repr": repr(raw)}
                attempt["status"] = "returned"
            return raw
        return call

    def kwargs(name):
        spec = deepcopy(frozen_plan["phases"][name])
        with lock:
            remaining = report["budget"]["max_provider_attempts"] - report["budget"]["actual_provider_attempts"]
        spec["max_calls"] = min(spec["max_calls"], remaining)
        spec.update(chat_json=provider(name), record=lambda event: emit(name, event))
        return spec

    def phase(name, operation):
        try:
            value = operation()
        except Exception as exc:
            partial = getattr(exc, "report", None)
            report["phases"][name] = {"execution": {"status": "orchestration_error",
                "error_type": type(exc).__name__, "message": str(exc)}, "partial_report": deepcopy(partial)}
            report["execution"] = {"status": "audit_error" if audit_failure or isinstance(exc, agent_editing.AuditRecordError)
                                   else "transport_error" if fatal
                                   else "orchestration_error", "phase": name}
            report["audit_failures"] = deepcopy(audit_failure)
            raise AgentCaseError("Agent case stopped; available audit retained", report) from exc
        report["phases"][name] = value
        if audit_failure or fatal:
            report["execution"] = {"status": "audit_error" if audit_failure else "transport_error", "phase": name}
            report["audit_failures"] = deepcopy(audit_failure)
            raise AgentCaseError("Audit/transport failure; no further provider calls", report)
        if on_phase:
            try:
                on_phase(deepcopy({"phase": name, "phase_report": value, "case_report": report}))
            except Exception as exc:
                report["execution"] = {"status": "audit_error", "phase": name, "callback": "on_phase"}
                report["audit_failures"] = [{"error_type": type(exc).__name__, "message": str(exc)}]
                raise AgentCaseError("Phase callback failed; available audit retained", report) from exc
        return value

    def skip(name, reason, *, relevant_inputs=None):
        return phase(name, lambda: {"execution": {"status": "skipped", "reason": reason},
            "inputs": _copy(relevant_inputs), "calls_used": 0, "result_scope": "research_only",
            "publication_effect": "none"})

    return phase, kwargs, skip


def run_agent_case(seed, design_intent, question_intent, public_protocol, *, plan,
                   chat_json, record=None, on_phase=None, review_reuse_context=None):
    """Run one material-edit and one question-edit cycle, with no hidden refill.

    ``plan`` has max_provider_attempts, material_batches, question_batches and
    phases for every name in PHASES. Each phase needs max_calls, max_input_chars,
    max_tokens, and model; question_review/final_review instead require explicit
    reader_model, reviewer_model and workers. An explicit reference_auditor_model
    selects the isolated three-call review; otherwise the two-call review is kept.
    Single editor/material-review
    budgets are zero or one. Batch formats are those of the existing series APIs.

    chat_json(step, messages, **params) must honor retries=1 as one provider
    attempt; an opaque client retrying internally cannot be counted here.
    record receives copied, phase-tagged trace events. on_phase receives a copied
    {phase, phase_report, case_report} after each phase. Callback failures halt
    all further calls and raise AgentCaseError with available raw artifacts.
    Exceptions from an actual injected provider call also halt all new calls;
    work already dispatched may finish and its returned artifacts are retained.

    Legacy plans keep diagnostic material work unchanged. With the explicit
    material_mode='team/v1', phases replace the three legacy material phases
    with material_team (models for every team role and a budget of 0..6).
    Only version-bound model acceptance permits question generation; it is not
    correctness proof. Scoring calibration and official release remain separate.

    Team plans may explicitly set review_reuse='same-input-within-run/v1'.
    Only an unchanged whole batch with completed, revalidated reviews and equal
    phase conditions can reuse this invocation's earlier opinions. The frozen
    runner supplies transport/source identities in review_reuse_context; absent
    or changed execution evidence conservatively keeps fresh review behavior.
    """
    frozen_plan = _plan(plan)
    active_phases = case_phases(frozen_plan)
    team_mode = frozen_plan.get("material_mode") == "team/v1"
    if not isinstance(public_protocol, str) or not public_protocol.strip():
        raise ValueError("An explicit nonempty public_protocol string is required")
    if not callable(chat_json) or any(cb is not None and not callable(cb) for cb in (record, on_phase)):
        raise ValueError("chat_json and supplied callbacks must be callable")
    inputs = _copy({"seed": seed, "design_intent": design_intent,
                    "question_intent": question_intent, "public_protocol": public_protocol})
    # Check generation inputs before the first provider call.
    material_series.prepare_material_series(inputs["seed"], inputs["design_intent"],
        model=frozen_plan["phases"]["material_generation"]["model"],
        batch_specs=frozen_plan["material_batches"], public_protocol=public_protocol)
    report = {"version": TEAM_VERSION if team_mode else VERSION, "result_scope": "research_only", "publication_effect": "none",
        "official_release": False, "inputs": inputs, "plan": frozen_plan,
        "binding": {"inputs_hash": digest(inputs), "plan_hash": digest(frozen_plan),
                    "implementation_hash": digest(Path(__file__).read_text(encoding="utf-8"))},
        "execution": {"status": "running"}, "phases": {}, "records": [], "provider_attempts": [],
        "budget": {"max_provider_attempts": frozen_plan["max_provider_attempts"],
                   "actual_provider_attempts": 0, "phase_provider_attempts": {name: 0 for name in active_phases}},
        "original_corpus": None, "current_corpus": None, "original_snapshot": None,
        "current_snapshot": None, "material_version_impact": None, "question_version_impact": None,
        "dispositions": {}, "receipt": None, "material_ready": False,
        "material_acceptance": {"status": "pending_attributed_decision", "authority": "none",
            "reason": "Material assessments and editor responses are fallible proposals, not acceptance."},
        "new_questions_from_revision": 0}
    phase, kwargs, skip = _orchestration(report, chat_json, record, on_phase)

    materials = phase("material_generation", lambda: material_series.generate_material_series(
        inputs["seed"], inputs["design_intent"], batch_specs=frozen_plan["material_batches"],
        public_protocol=public_protocol, **kwargs("material_generation")))
    report["original_corpus"] = deepcopy(materials.get("corpus"))
    if not materials["downstream_ready"]:
        for name in active_phases[1:]:
            skip(name, "material_transport_incomplete", relevant_inputs={"partial_corpus": materials["partial_corpus"]})
        report["execution"] = {"status": "stopped", "reason": "material_transport_incomplete"}
        report["receipt"] = {"result_scope": "research_only", "publication_effect": "none",
            "official_release": False, "material_ready": False, "scoring_ready": False,
            "original_count": 0, "rows": [], "reason": "No question generation on incomplete material transport"}
        return report
    corpus = deepcopy(materials["corpus"])
    if team_mode:
        from pipeline.material_team import run_material_team

        def team_checkpoint(event):
            report["phases"]["material_team"] = deepcopy(event["case_report"])
            report["current_corpus"] = deepcopy(event["case_report"]["candidate_corpus"])
            if on_phase:
                on_phase(deepcopy({"phase": "material_team." + event["phase"],
                    "phase_report": event["phase_report"], "case_report": report,
                    "material_team_report": event["case_report"]}))

        team = phase("material_team", lambda: run_material_team(corpus, public_protocol,
            on_phase=team_checkpoint, **kwargs("material_team")))
        corpus = deepcopy(team["candidate_corpus"])
        report["current_corpus"] = deepcopy(corpus)
        report["material_acceptance"] = deepcopy(team["acceptance"])
        report["material_acceptance"]["review_phase"] = "material_team.decision"
        accepted = team["accepted_for_question_generation"] is True
        if accepted and (team["acceptance"].get("corpus_hash") != digest(corpus)
                or team["acceptance"].get("protocol_hash") != digest(public_protocol)
                or team["acceptance"].get("authority") != "model"
                or team["acceptance"].get("correctness_verified") is not False):
            report["execution"] = {"status": "acceptance_binding_error", "phase": "material_team"}
            raise AgentCaseError("Material acceptance does not bind to this public version", report)
        report["material_ready"] = accepted
        if not accepted:
            reason = "material_team_held" if team["acceptance"].get("status") == "held" else "material_team_incomplete"
            for name in active_phases[2:]:
                skip(name, reason, relevant_inputs={"corpus": corpus,
                    "material_acceptance": report["material_acceptance"]})
            report["execution"] = {"status": "stopped", "reason": reason,
                "material_team_execution": deepcopy(team["execution"])}
            report["semantic_status"] = "research_only_material_not_accepted_for_question_generation"
            report["receipt"] = {"result_scope": "research_only", "publication_effect": "none",
                "official_release": False, "material_ready": False, "scoring_ready": False,
                "material_acceptance": deepcopy(report["material_acceptance"]),
                "original_count": 0, "rows": [], "reason": reason}
            return report
    else:
        initial_material_review = phase("material_review", lambda: agent_editing.review_materials(
            corpus, public_protocol, **kwargs("material_review")))
        if initial_material_review["proposal_ready"]:
            material_edit = phase("material_edit", lambda: agent_editing.propose_material_revision(
                corpus, public_protocol, initial_material_review["issues"], **kwargs("material_edit")))
        else:
            material_edit = skip("material_edit", "no_transport_valid_independent_material_review",
                                 relevant_inputs={"corpus": corpus, "review": initial_material_review})
        if material_edit.get("proposal_ready") and material_edit.get("proposed_corpus") is not None:
            corpus = deepcopy(material_edit["proposed_corpus"])
            phase("material_rereview", lambda: agent_editing.review_materials(
                corpus, public_protocol, **kwargs("material_rereview")))
            report["material_acceptance"]["review_phase"] = "material_rereview"
        else:
            skip("material_rereview", "material_version_not_changed", relevant_inputs={"corpus": corpus})
            report["material_acceptance"]["review_phase"] = "material_review"
    report["current_corpus"] = deepcopy(corpus)
    report["material_acceptance"]["corpus_hash"] = digest(corpus)
    proposals = phase("question_generation", lambda: question_series.propose_question_series(
        inputs["seed"], inputs["question_intent"], corpus, public_protocol,
        batch_specs=frozen_plan["question_batches"], **kwargs("question_generation")))
    original = quality_workflow.snapshot(proposals["questions"], corpus, public_protocol)
    report["original_snapshot"] = original
    before_material = quality_workflow.snapshot(proposals["questions"], materials["corpus"], public_protocol)
    report["material_version_impact"] = {
        "hypothetical_predictions_only": True,
        "explanation": "No predictions were generated before material editing; any externally supplied old-context prediction would be stale.",
        "before_snapshot": before_material, "after_snapshot_id": original["snapshot_id"],
        "by_qid": quality_workflow.change_impact(before_material, original)}
    return _question_cycle(report, original, {
        "original_candidate_count": len(proposals["candidates"]),
        "unreadable_candidate_count": proposals["quantity"]["unreadable_candidates"],
        "requested_question_count": proposals["quantity"]["requested"]},
        phase=phase, kwargs=kwargs, skip=skip, team_mode=team_mode,
        review_reuse_context=review_reuse_context)


def prepare_question_cycle(original_snapshot, upstream_report, plan):
    """Bind a fresh cycle to retained originals from a failed team case.

    This validates provenance and counts, not the truth of model acceptance.
    No cached question review, editor decision or grade is imported.
    """
    original, upstream, frozen_plan = _copy(original_snapshot), _copy(upstream_report), _copy(plan)
    quality_workflow.validate_snapshot(original)
    if (not isinstance(frozen_plan, dict) or not {"max_provider_attempts", "phases"} <= set(frozen_plan)
            or set(frozen_plan) - {"max_provider_attempts", "phases", "review_reuse"}
            or type(frozen_plan["max_provider_attempts"]) is not int
            or not 0 <= frozen_plan["max_provider_attempts"] <= 27
            or not isinstance(frozen_plan["phases"], dict)
            or set(frozen_plan["phases"]) != set(QUESTION_CYCLE_PHASES)):
        raise ValueError("Question cycle requires five explicit phases and at most 27 attempts")
    _validate_reuse_policy(frozen_plan, team_mode=True)
    _validate_phase_specs(frozen_plan["phases"])
    for name in REVIEW_PHASES:
        spec = frozen_plan["phases"][name]
        if not spec.get("reference_auditor_model") or spec["max_calls"] > 3 * len(original["questions"]):
            raise ValueError("Continuation requires fresh three-role review without refill")
    if sum(s["max_calls"] for s in frozen_plan["phases"].values()) > 27:
        raise ValueError("Combined phase reservations must not exceed 27")
    initial_failure = (upstream.get("execution", {}).get("phase") == "question_review"
        and upstream["execution"].get("status") in {"orchestration_error", "audit_error", "transport_error"})
    completed_gaps = upstream.get("execution", {}).get("status") == "completed_with_execution_gaps"
    if (upstream.get("version") != TEAM_VERSION or upstream.get("result_scope") != "research_only"
            or upstream.get("official_release") is not False
            or not (initial_failure or completed_gaps)
            or upstream.get("original_snapshot") != original
            or original.get("parent_snapshot_id") is not None):
        raise ValueError("Continuation requires a failed team case with its original question snapshot")
    if initial_failure and (upstream.get("current_snapshot") is not None
            or upstream.get("dispositions") != {} or upstream.get("receipt") is not None
            or any(name in upstream["phases"] for name in QUESTION_CYCLE_PHASES[1:])):
        raise ValueError("Initial-review failure must precede any question editing or later cycle")
    prior_plan = _plan(upstream.get("plan"))
    if prior_plan.get("material_mode") != "team/v1":
        raise ValueError("Continuation needs explicitly accepted team material")
    if (upstream.get("binding", {}).get("plan_hash") != digest(prior_plan)
            or upstream["binding"].get("inputs_hash") != digest(upstream.get("inputs"))):
        raise ValueError("Upstream case input/plan binding differs")
    corpus, protocol = original["corpus"], original["protocol"]
    if not protocol.strip() or upstream.get("current_corpus") != corpus or upstream["inputs"]["public_protocol"] != protocol:
        raise ValueError("Original snapshot differs from accepted public material/protocol")
    team = upstream.get("phases", {}).get("material_team", {})
    acceptance = upstream.get("material_acceptance", {})
    if (upstream.get("material_ready") is not True or team.get("accepted_for_question_generation") is not True
            or team.get("candidate_corpus") != corpus or team.get("candidate_hash") != digest(corpus)
            or acceptance != {**team.get("acceptance", {}), "review_phase": "material_team.decision"}
            or acceptance.get("status") != "accepted" or acceptance.get("authority") != "model"
            or acceptance.get("correctness_verified") is not False
            or acceptance.get("corpus_hash") != digest(corpus) or acceptance.get("protocol_hash") != digest(protocol)):
        raise ValueError("Material acceptance does not bind to the original snapshot")
    budget = upstream.get("budget", {})
    counts = budget.get("phase_provider_attempts", {})
    prior_attempts = upstream.get("provider_attempts", [])
    if (set(counts) != set(TEAM_PHASES) or any(type(v) is not int or v < 0 for v in counts.values())
            or any(counts[name] > prior_plan["phases"][name]["max_calls"] for name in counts)
            or (initial_failure and any(counts[name] != 0 for name in QUESTION_CYCLE_PHASES[1:]))
            or type(budget.get("actual_provider_attempts")) is not int
            or budget.get("max_provider_attempts") != prior_plan["max_provider_attempts"]
            or not 0 <= budget["actual_provider_attempts"] <= budget["max_provider_attempts"]
            or budget["actual_provider_attempts"] != sum(counts.values())
            or len(prior_attempts) != budget["actual_provider_attempts"]
            or any(sum(a.get("phase") == name for a in prior_attempts) != count for name, count in counts.items())
            or any(a.get("attempt") != index + 1 for index, a in enumerate(prior_attempts))
            or any(a.get("status") not in {"returned", "provider_error"}
                   or a.get("params", {}).get("retries") != 1 for a in prior_attempts)):
        raise ValueError("Upstream failed lineage must retain its exact terminal attempt counts")
    if completed_gaps:
        current = upstream.get("current_snapshot")
        dispositions, receipt = upstream.get("dispositions", {}), upstream.get("receipt")
        edit = upstream["phases"].get("question_edit", {})
        expected_current = quality_workflow.snapshot(original["questions"], corpus, protocol, parent=original)
        if (current != expected_current or set(dispositions) != set(original["identities"])
                or edit.get("proposal_ready") is not False or edit.get("candidate_ready") is not False
                or edit.get("decisions") != [] or edit.get("proposed_questions") != []
                or edit.get("execution", {}).get("status") == "ok"
                or not upstream["execution"].get("gaps")
                or not any(a["phase"] in QUESTION_CYCLE_PHASES and a["status"] == "provider_error"
                           for a in prior_attempts)):
            raise ValueError("Completed recovery requires unchanged originals and an unsuccessful editor after provider failure")
        for q in original["questions"]:
            disposition = dispositions[q["qid"]]
            if (disposition.get("qid") != q["qid"] or disposition.get("before") != q
                    or disposition.get("after") != q or disposition.get("quality_approval") is not False
                    or disposition.get("action") != "retained_original_editor_incomplete"):
                raise ValueError("Recovery cannot discard an applied question edit or disposition")
        rebuilt = quality_workflow.make_receipt(original, current, upstream["phases"].get("final_review"),
            material_ready=True, dispositions=dispositions)
        if (not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in rebuilt.items())
                or rebuilt["counts"]["unresolved"] != len(original["questions"])
                or receipt.get("material_acceptance") != acceptance
                or receipt.get("editor_dispositions") != dispositions
                or upstream.get("question_version_impact") != quality_workflow.change_impact(original, current)):
            raise ValueError("Completed recovery must retain an entirely unresolved, version-bound original receipt")
    generated = upstream["phases"].get("question_generation", {})
    frozen = generated.get("frozen_inputs", {})
    if (frozen.get("corpus") != corpus or frozen.get("public_protocol") != protocol
            or frozen.get("batch_specs") != prior_plan["question_batches"]
            or frozen.get("seed") != upstream["inputs"]["seed"]
            or frozen.get("design_intent") != upstream["inputs"]["question_intent"]
            or generated.get("binding", {}).get("frozen_inputs_hash") != digest(frozen)
            or generated.get("binding_hash") != digest(generated.get("binding"))):
        raise ValueError("Upstream question generation inputs/binding differ")
    candidates, questions = [], []
    for index, batch in enumerate(generated.get("batch_reports", [])):
        c, q = question_series._collect_batch(batch, index, generated["binding_hash"])
        candidates.extend(c)
        questions.extend(q)
    requested = sum(s["count"] for s in prior_plan["question_batches"])
    quantity = question_series._quantity(requested, candidates, questions)
    output_hash = digest({"binding_hash": generated["binding_hash"], "batches": [
        {key: batch[key] for key in ("binding", "raw_output", "execution")}
        for batch in generated.get("batch_reports", [])]})
    if (generated.get("candidates") != candidates or generated.get("questions") != questions
            or generated.get("quantity") != quantity or generated.get("series_output_hash") != output_hash
            or quality_workflow.snapshot(questions, corpus, protocol) != original):
        raise ValueError("Original candidates, structured references or denominator differ from raw generation")
    denominator = {"original_candidate_count": len(candidates),
        "unreadable_candidate_count": quantity["unreadable_candidates"], "requested_question_count": requested}
    if completed_gaps and any(upstream["receipt"].get(k) != v for k, v in denominator.items()):
        raise ValueError("Original receipt denominator differs from raw generation")
    upstream_kind = "initial_review_failure" if initial_failure else "completed_unedited_execution_gaps"
    return {"original_snapshot": original, "upstream_report": upstream, "plan": frozen_plan,
        "denominator": denominator, "binding": {"original_snapshot_id": original["snapshot_id"],
            "upstream_kind": upstream_kind,
            "upstream_report_hash": digest(upstream), "upstream_execution": deepcopy(upstream["execution"]),
            "upstream_budget": deepcopy(budget), "upstream_provider_attempts": budget["actual_provider_attempts"],
            "upstream_generation_hash": digest(generated), "material_acceptance_hash": digest(acceptance),
            "plan_hash": digest(frozen_plan), "denominator": deepcopy(denominator)}}


def run_agent_question_cycle(original_snapshot, upstream_report, *, plan, chat_json,
                             record=None, on_phase=None, before_call=None, review_reuse_context=None):
    """Run a separately budgeted, strict continuation; never regenerate questions."""
    if not callable(chat_json) or any(cb is not None and not callable(cb) for cb in (record, on_phase, before_call)):
        raise ValueError("chat_json and supplied callbacks must be callable")
    prepared = prepare_question_cycle(original_snapshot, upstream_report, plan)
    original, upstream, frozen_plan = (prepared[k] for k in ("original_snapshot", "upstream_report", "plan"))
    report = {"version": QUESTION_CYCLE_VERSION, "result_scope": "research_only", "publication_effect": "none",
        "official_release": False, "run_kind": "derived_question_cycle", "plan": frozen_plan,
        "binding": prepared["binding"], "execution": {"status": "running"}, "phases": {}, "records": [],
        "provider_attempts": [], "budget": {"max_provider_attempts": frozen_plan["max_provider_attempts"],
            "actual_provider_attempts": 0, "phase_provider_attempts": {name: 0 for name in QUESTION_CYCLE_PHASES}},
        "upstream_budget": deepcopy(upstream["budget"]), "original_corpus": deepcopy(upstream["original_corpus"]),
        "current_corpus": deepcopy(original["corpus"]), "original_snapshot": original, "current_snapshot": None,
        "material_version_impact": deepcopy(upstream.get("material_version_impact")), "question_version_impact": None,
        "dispositions": {}, "receipt": None, "material_ready": True,
        "material_acceptance": deepcopy(upstream["material_acceptance"]), "new_questions_from_revision": 0}
    phase, kwargs, skip = _orchestration(report, chat_json, record, on_phase, strict=True, before_call=before_call)
    return _question_cycle(report, original, prepared["denominator"],
        phase=phase, kwargs=kwargs, skip=skip, team_mode=True, review_reuse_context=review_reuse_context)


def _question_cycle(report, original, denominator, *, phase, kwargs, skip, team_mode,
                    review_reuse_context=None):
    """The one question edit/fresh-review cycle used by generation and continuation."""
    corpus, public_protocol = original["corpus"], original["protocol"]
    reuse_enabled = report["plan"].get("review_reuse") == REVIEW_REUSE_POLICY
    initial_stamp = (_reuse_stamp(report["plan"], review_reuse_context)[0] if reuse_enabled else None)
    initial_review = phase("question_review", lambda: quality_workflow.fresh_review(original, **kwargs("question_review")))
    collection = None
    if team_mode:
        from pipeline.question_set_review import review_question_set
        collection = (phase("collection_review", lambda: review_question_set(
            original["questions"], corpus, public_protocol, **kwargs("collection_review")))
            if original["questions"] else skip("collection_review", "no_readable_question_candidates"))
    issues = _editor_issues(initial_review, collection,
        documents=quality_workflow.visible_documents(corpus)[0])
    report["question_editor_issues"] = issues
    if original["questions"]:
        edit = phase("question_edit", lambda: agent_editing.propose_question_revisions(
            original["questions"], corpus, public_protocol, issues, **kwargs("question_edit")))
    else:
        edit = skip("question_edit", "no_readable_question_candidates", relevant_inputs={"questions": []})
    if edit.get("proposal_ready") or (team_mode and edit.get("candidate_ready") is True):
        current_questions = edit["proposed_questions"]
        report["dispositions"] = {d["qid"]: deepcopy(d) for d in edit["decisions"]}
    else:
        current_questions = original["questions"]
        report["dispositions"] = {q["qid"]: {"qid": q["qid"], "action": "retained_original_editor_incomplete",
            "reason": "No transport-valid complete editorial disposition; original retained for final diagnostic review.",
            "before": deepcopy(q), "after": deepcopy(q), "quality_approval": False}
            for q in original["questions"]}
    current = quality_workflow.snapshot(current_questions, corpus, public_protocol, parent=original)
    report["current_snapshot"] = current
    report["question_version_impact"] = quality_workflow.change_impact(original, current)
    reused = (_reuse_report(report, "question_review", "final_review", initial_review, original, current,
        initial_stamp=initial_stamp, context=review_reuse_context) if reuse_enabled else None)
    final = phase("final_review", lambda: reused if reused is not None
                  else quality_workflow.fresh_review(current, **kwargs("final_review")))
    final_collection = None
    if team_mode:
        reused_collection = (_reuse_report(report, "collection_review", "final_collection_review", collection,
            original, current, initial_stamp=initial_stamp, context=review_reuse_context, collection=True)
            if reuse_enabled and current["questions"] else None)
        final_collection = (phase("final_collection_review", lambda: reused_collection if reused_collection is not None
            else review_question_set(current["questions"], corpus, public_protocol, **kwargs("final_collection_review")))
            if current["questions"] else skip("final_collection_review", "no_retained_question_candidates"))
    try:
        report["receipt"] = quality_workflow.make_receipt(original, current, final,
            material_ready=report["material_ready"], dispositions=report["dispositions"])
    except Exception as exc:
        report["execution"] = {"status": "receipt_validation_error", "error_type": type(exc).__name__, "message": str(exc)}
        raise AgentCaseError("Cannot validate final research receipt; artifacts retained", report) from exc
    report["receipt"].update(material_acceptance=deepcopy(report["material_acceptance"]),
        editor_dispositions=deepcopy(report["dispositions"]),
        **deepcopy(denominator),
        new_questions_from_revision=0, final_review_is_fresh=reused is None)
    if reuse_enabled:
        report["receipt"]["within_run_review_reuse"] = deepcopy(report.get("within_run_review_reuse", {}))
    if team_mode:
        report["receipt"]["editorial_completion"] = deepcopy(edit.get("editorial_completion"))
        report["receipt"]["collection_assessment"] = {
            "source": "fallible_model_opinions", "correctness_verified": False,
            "improvement_verified": False,
            "original": {"snapshot_id": original["snapshot_id"], "report": deepcopy(collection)},
            "final": {"snapshot_id": current["snapshot_id"], "report": deepcopy(final_collection)},
            "scope": "Coverage and overlap opinions are separate from item correctness and measured difficulty. "
                     "Item model_review_eligible counts do not establish collection improvement. "
                     + (("The final collection opinion reuses the unchanged initial opinion; no new reading occurred. "
                         if reused_collection is not None else
                         "A fresh final collection reader sees only the current public material, protocol and question proposals. ")
                        + "Neither opinion triggers a further edit cycle." if reuse_enabled else
                        "The final collection reader sees only the current public material, protocol and question proposals; "
                        "its opinion triggers no further edit cycle.")}
    execution_gaps = []
    for name, value in report["phases"].items():
        state = value.get("execution", {}).get("status")
        if state and state not in {"ok", "completed", "skipped"}:
            execution_gaps.append({"phase": name, "execution": deepcopy(value["execution"])})
        if name in REVIEW_PHASES:
            for item in value["items"]:
                failed_stages = [(stage, execution) for stage, execution in item.get("stage_execution", {}).items()
                                 if execution.get("status") != "ok"]
                execution_gaps.extend({"phase": name, "qid": item["source_qid"],
                    "stage": stage, "execution": deepcopy(execution)} for stage, execution in failed_stages)
                if not failed_stages and item["execution"]["status"] != "ok":
                    execution_gaps.append({"phase": name, "qid": item["source_qid"],
                        "execution": deepcopy(item["execution"])})
    report["execution"] = {"status": "completed_with_execution_gaps" if execution_gaps else "completed",
                           "gaps": execution_gaps}
    report["semantic_status"] = ("research_only_model_material_acceptance_pending_scoring_calibration" if team_mode
                                 else "research_only_pending_material_acceptance_and_scoring_calibration")
    return report
