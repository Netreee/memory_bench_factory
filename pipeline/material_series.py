"""Bounded, chronological material batches for an isolated research case.

No configuration or network provider is loaded here. A caller owns persistence
and any exact-input replay; this module never manufactures resumed execution.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Callable

from eval.provenance import digest
from pipeline import material_proposals as single_batch
from pipeline.semantic_review import visible_documents


VERSION = "seed-material-series/v1"
SERIES_INSTRUCTIONS = """这是同一业务案例按预定日期递进生成的一个批次，不是另起一个互不关联的案例。
只返回当前 scheduled_batch 指定 session_id、date 的一个 session；其 docs 是本批全部新正文，
不要重复交付或改写以前的文档，不要另建其他 session。document_count 与 target_chars 是预定规模
目标，target_chars 指本批所有 content 的总字符量，不含标题和作者说明；用有实际用途的信息达到
目标，不靠复制、字段堆砌或无用填充。不能达到时如实说明，不虚构已完成数量。
previous_public_corpus 提供此前已交付的完整公开正文，保持这些原文不变。它是连续性的公开依据，
其中仍可能包含不同人的主张、计划、回顾或不完整信息，不能把所有句子都当作全知真值。
previous_writer_notes 是按来源批次列出的完整作者解释，仅为生成者连续性参考，不会给答题者。
这些解释可能错误、过强或缺证据，不是已证实世界事实；尤其不能把作者说的‘没有发生’当成
公开材料已经证明事件未发生。若作者解释与公开正文不符，以可公开检验的材料为依据并说明缺口，
不要静默改写旧文或把解释补成既往事实。新事实必须通过本批有来源、有语境的材料自身成立。
series_design_intent 和 seed 仍只是设计目标，不是答案依据。各批可以逐步实现目标，但不强迫
每一期显式铺满所有机制。允许有目的的日常沟通、未知和有限视角，不得为了答案而补齐世界清单。
当前日期之后尚未发生的事项只能按当事人当时可知的计划、预测、期待或不确定表述；不要把未来
完成状态冒充本期已完成事实。允许有来源的回顾、晚到材料及正文中的其他日期，不要求正文日期
都等于外层session日期，实际时间含义由上下文决定。给定固定public_protocol不得被建议协议覆盖。
design_notes 可保留下一期需注意的自然设定、尚未兑现的目标与证据缺口，但必须与公开正文分开，
也不能用它替代公开业务规则或最终答案。只交付当前批次，不生成问题或标准答案。
"""


def _copy(value):
    return single_batch._copy(value)


def _validate_specs(batch_specs):
    if not isinstance(batch_specs, list) or not batch_specs:
        raise ValueError("batch_specs must be a nonempty list")
    specs = _copy(batch_specs)
    last_id, last_date = None, None
    for index, spec in enumerate(specs):
        if not isinstance(spec, dict):
            raise ValueError(f"batch_specs[{index}] must be an object")
        sid, scheduled_date = spec.get("session_id"), spec.get("date")
        if type(sid) is not int or (last_id is not None and sid <= last_id):
            raise ValueError("Scheduled session IDs must be unique, increasing integers")
        try:
            if not isinstance(scheduled_date, str) or date.fromisoformat(scheduled_date).isoformat() != scheduled_date:
                raise ValueError()
        except ValueError:
            raise ValueError("Scheduled dates must be ISO calendar dates YYYY-MM-DD") from None
        if last_date is not None and scheduled_date < last_date:
            raise ValueError("Scheduled dates must be in nondecreasing order")
        if not isinstance(spec.get("intent"), str) or not spec["intent"].strip():
            raise ValueError("Each batch needs nonempty natural-language intent")
        for field in ("document_count", "target_chars"):
            single_batch._positive(spec.get(field), field)
        last_id, last_date = sid, scheduled_date
    return specs


def prepare_material_series(seed, design_intent, *, model: str, batch_specs,
                            public_protocol) -> dict:
    """Freeze caller inputs and implementations without generating documents.

    Each spec contains session_id (integer), date (ISO day), intent (text),
    document_count and target_chars (positive integers). Targets are observable
    goals, not claims of achieved scale or semantic validation.
    """
    specs = _validate_specs(batch_specs)
    if not isinstance(public_protocol, (str, dict)):
        raise ValueError("A fixed text or object public_protocol is required")
    validated = single_batch.prepare_materials(seed, design_intent, model=model,
        count=specs[0]["document_count"], public_protocol=public_protocol)
    inputs = _copy({"seed": seed, "design_intent": design_intent, "batch_specs": specs,
                    "model": model, "public_protocol": public_protocol})
    binding = {"version": VERSION, "inputs_hash": digest(inputs),
        "seed_hash": digest(seed), "design_intent_hash": digest(design_intent),
        "batch_specs_hash": digest(specs), "model": model,
        "protocol_hash": digest(public_protocol), "series_instructions_hash": digest(SERIES_INSTRUCTIONS),
        "implementation_hash": digest(Path(__file__).read_text(encoding="utf-8")),
        "single_batch_version": single_batch.VERSION,
        "single_batch_prompt_hash": validated["binding"]["prompt_hash"],
        "single_batch_implementation_hash": validated["binding"]["implementation_hash"]}
    return {"version": VERSION, "mode": "prepared", "result_scope": "research_only",
        "publication_effect": "none", "review_state": "pending_independent_material_review",
        "binding": binding, "series_inputs": inputs, "series_instructions": SERIES_INSTRUCTIONS,
        "public_protocol": _copy(public_protocol), "public_protocol_source": "caller_fixed",
        "protocol_review_state": "caller_fixed_not_reviewed_here", "corpus": None,
        "partial_corpus": {"sessions": []}, "batch_reports": [], "batch_observations": [],
        "writer_notes": [], "calls_used": 0, "records": [],
        "execution": {"status": "not_executed"}, "downstream_ready": False,
        "downstream_ready_scope": "transport_only_not_semantic_approval",
        "continuity_authority": "previous_public_material_with_attribution; writer_notes_are_fallible_generator_only_hypotheses",
        "scale_observations": {"requested_batches": len(specs), "attempted_batches": 0,
            "completed_batches": 0, "requested_documents": sum(s["document_count"] for s in specs),
            "requested_body_chars": sum(s["target_chars"] for s in specs),
            "public_documents": 0, "public_body_chars": 0,
            "all_scheduled_batches_completed": False, "scale_targets_met": False,
            "character_measure": "Python len of public content strings only; excludes titles, notes, JSON and metadata"}}


def _scheduled_projection_issues(batch, spec):
    """Only check transport scope and schedule identity, never body meaning."""
    issues = []
    if batch["execution"]["status"] != "ok":
        issues.append("batch_execution_not_ok")
    if not batch["downstream_ready"] or batch["corpus"] is None:
        issues.append("whole_batch_not_transport_ready")
        return issues
    sessions = batch["corpus"]["sessions"]
    if len(sessions) != 1:
        issues.append("batch_must_have_exactly_one_scheduled_session")
    else:
        session = sessions[0]
        if type(session.get("session_id")) is not int or session["session_id"] != spec["session_id"]:
            issues.append("scheduled_session_id_mismatch_no_renumbering")
        if session.get("date") != spec["date"]:
            issues.append("scheduled_date_mismatch_no_time_inference")
    return issues


def generate_material_series(seed, design_intent, *, model: str, batch_specs,
                             public_protocol, chat_json: Callable, max_calls: int,
                             max_input_chars: int, max_tokens: int,
                             record: Callable | None = None,
                             on_batch: Callable | None = None) -> dict:
    """Run once per scheduled batch, stopping on failure or unprojectable scope.

    on_batch receives one deep-copied dict containing batch_index, batch_report
    and series_report. Callback failures propagate before any further model call.
    Resume/replay belongs to a caller that verifies exact frozen messages,
    parameters, bindings and provider attempts; no unverified prior report is
    accepted by this API.
    """
    report = prepare_material_series(seed, design_intent, model=model,
        batch_specs=batch_specs, public_protocol=public_protocol)
    for value, name in ((max_calls, "max_calls"), (max_input_chars, "max_input_chars"),
                        (max_tokens, "max_tokens")):
        single_batch._positive(value, name, zero=name == "max_calls")
    inputs = report["series_inputs"]
    specs = inputs["batch_specs"]
    report.update(mode="executed", budget={"max_calls": max_calls,
        "max_input_chars_per_batch": max_input_chars, "max_output_tokens_per_batch": max_tokens,
        "maximum_logical_calls": min(max_calls, len(specs)), "automatic_retries_or_repairs": False})

    for index, spec in enumerate(specs):
        batch_intent = {"series_version": VERSION, "series_binding_hash": digest(report["binding"]),
            "series_design_intent": _copy(inputs["design_intent"]), "scheduled_batch": _copy(spec),
            "series_instructions": SERIES_INSTRUCTIONS,
            "previous_public_corpus": _copy(report["partial_corpus"]),
            "previous_writer_notes": _copy(report["writer_notes"]),
            "writer_notes_authority": "generator_only_fallible_design_hypotheses_not_public_evidence"}

        def batch_record(event):
            enriched = {"series_version": VERSION, "series_binding_hash": digest(report["binding"]),
                        "batch_index": index, "scheduled_batch": _copy(spec), **_copy(event)}
            report["records"].append(_copy(enriched))
            if record is not None:
                record(deepcopy(enriched))

        batch = single_batch.propose_materials(inputs["seed"], batch_intent,
            model=inputs["model"], count=spec["document_count"], public_protocol=inputs["public_protocol"],
            chat_json=chat_json, max_calls=1 if report["calls_used"] < max_calls else 0,
            max_input_chars=max_input_chars, max_tokens=max_tokens, record=batch_record)
        report["batch_reports"].append(_copy(batch))
        report["calls_used"] += batch["calls_used"]
        issues = _scheduled_projection_issues(batch, spec)
        raw_candidates = batch.get("candidates", [])
        readable_chars = sum(len(c["raw_document"]["content"]) for c in raw_candidates if c["body_readable"])
        observation = {"batch_index": index, "scheduled_batch": _copy(spec),
            "batch_binding_hash": digest(batch["binding"]), "batch_hash": batch.get("batch_hash"),
            "received_documents": len(raw_candidates), "readable_body_chars": readable_chars,
            "document_difference": len(raw_candidates) - spec["document_count"],
            "body_char_difference": readable_chars - spec["target_chars"],
            "document_target_met": len(raw_candidates) >= spec["document_count"],
            "body_char_target_met": readable_chars >= spec["target_chars"],
            "appended_to_public_prefix": not issues, "scope_issues": issues}
        report["batch_observations"].append(observation)
        if not issues:
            report["partial_corpus"]["sessions"].extend(_copy(batch["corpus"]["sessions"]))
        # Even a rejected batch's author notes stay in the audit. Because the
        # series stops, these notes never become input to a later batch.
        report["writer_notes"].append({"batch_index": index, "session_id": spec["session_id"],
            "date": spec["date"], "batch_hash": batch.get("batch_hash"),
            "authority": "generator_only_fallible_design_hypotheses_not_public_evidence",
            "design_notes": _copy(batch.get("design_notes")), "limitations": _copy(batch.get("limitations", []))})
        scale = report["scale_observations"]
        scale["attempted_batches"] = len(report["batch_reports"])
        scale["completed_batches"] = len(report["partial_corpus"]["sessions"])
        public_docs = [d for s in report["partial_corpus"]["sessions"] for d in s["docs"]]
        scale["public_documents"], scale["public_body_chars"] = len(public_docs), sum(len(d["content"]) for d in public_docs)
        scale["all_scheduled_batches_completed"] = not issues and index == len(specs) - 1
        scale["scale_targets_met"] = scale["all_scheduled_batches_completed"] and all(
            o["document_target_met"] and o["body_char_target_met"] for o in report["batch_observations"])
        if issues:
            report["execution"] = {"status": "stopped", "batch_index": index,
                "reason": batch["execution"]["status"] if batch["execution"]["status"] != "ok" else "schedule_scope_mismatch",
                "scope_issues": issues}
        elif scale["all_scheduled_batches_completed"]:
            report["corpus"] = _copy(report["partial_corpus"])
            report["downstream_ready"] = True
            report["execution"] = {"status": "completed", "scale_targets_met": scale["scale_targets_met"]}
            documents, _ = visible_documents(report["corpus"], include_titles=False)
            report["public_binding"] = {"corpus_hash": digest(report["corpus"]),
                "visible_corpus_hash": digest(documents), "protocol_hash": digest(report["public_protocol"]),
                "visible_view": {"include_titles": False},
                "authority": "candidate_material_not_validated_business_truth"}
        else:
            report["execution"] = {"status": "in_progress", "last_completed_batch_index": index}
        if on_batch is not None:
            on_batch(deepcopy({"batch_index": index, "batch_report": batch, "series_report": report}))
        if issues:
            break
    return report
