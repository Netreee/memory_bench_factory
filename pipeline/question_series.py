"""Serial research question proposals over one frozen solver-visible corpus.

No semantic gate, deduplication, repair, publication, or provider configuration
is invoked here. Each batch delegates at most one call to question_proposals.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Callable

from eval.provenance import digest
from pipeline.question_proposals import prepare_proposals, propose_questions


VERSION = "corpus-first-question-series/v1"
AUTHOR_CONTEXT = (
    "series_context 中的 focus 是本批自由设计焦点，previous_questions 是此前所有可读候选的完整题面，"
    "仅供避免重复，不是公开事实或已经审定的题目。可以质疑此前设计，不继承其前提或答案。"
    "按现有公开材料自由提出本批题目；不必满足固定题型，不通过换词重复凑数。"
    "若材料不能支持更多有意义题目，如实报告缺额。所有参考仍是可被推翻的提案。"
)


def _copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _integer(value, name, *, zero=False):
    if type(value) is not int or value < (0 if zero else 1):
        raise ValueError(f"{name} must be an integer >= {0 if zero else 1}")


def _batch_specs(value):
    if not isinstance(value, list) or not value:
        raise ValueError("batch_specs must be a nonempty ordered list")
    specs = _copy(value)
    for index, spec in enumerate(specs):
        if not isinstance(spec, dict):
            raise ValueError(f"batch_specs[{index}] must be an object")
        if not isinstance(spec.get("focus"), str) or not spec["focus"].strip():
            raise ValueError(f"batch_specs[{index}].focus must be nonempty text")
        _integer(spec.get("count"), f"batch_specs[{index}].count")
    return specs


def _rows(batch):
    """Locate explicit candidate containers without interpreting arbitrary text."""
    output = batch.get("raw_output")
    if isinstance(output, dict) and isinstance(output.get("questions"), list):
        return output["questions"], "/questions", []
    if isinstance(output, list):
        return output, "", ["series_transfer:top_level_candidate_list"]
    if isinstance(output, dict) and isinstance(output.get("questions"), (dict, str)):
        return [output["questions"]], "/questions", ["series_transfer:questions_container_not_list"]
    return [], None, []


def _collect_batch(batch, index, series_binding_hash):
    """Preserve readable candidates even when the proposal envelope is invalid."""
    rows, container, envelope_issues = _rows(batch)
    base_candidates = {c["candidate_id"]: c for c in batch["candidates"]}
    base_questions = {q["qid"]: q for q in batch["questions"]}
    candidates, questions = [], []
    for position, raw in enumerate(rows):
        local_id = f"p{position + 1:06d}"
        qid = f"s{index + 1:03d}{local_id}"
        existing = base_candidates.get(local_id)
        issues = list(existing["format_issues"]) if existing else []
        issues.extend(envelope_issues)
        text = raw.get("question") if isinstance(raw, dict) else raw if isinstance(raw, str) else None
        readable = isinstance(text, str) and bool(text.strip())
        if isinstance(raw, str):
            issues.append("series_transfer:string_candidate_not_object")
        if not readable and "question_must_be_nonempty_text" not in issues:
            issues.append("question_must_be_nonempty_text")
        original = base_questions.get(local_id)
        origin = deepcopy(original["origin"]) if original else {
            "proposal_version": batch["version"], "batch_hash": batch.get("batch_hash"),
            "output_hash": digest(batch.get("raw_output")), "candidate_index": position,
            "candidate_id": local_id,
        }
        source_path = (f"{container}/{position}" if not envelope_issues or container == ""
                       else container)
        origin["series"] = {
            "version": VERSION, "binding_hash": series_binding_hash, "batch_index": index,
            "source_qid": original["qid"] if original else None, "source_path": source_path,
            "transfer": "proposal_review_input" if original else "readable_candidate_recovery",
        }
        candidates.append({"qid": qid, "batch_index": index, "candidate_index": position,
            "raw_proposal": _copy(raw), "format_issues": issues,
            "review_input_ready": readable, "origin": deepcopy(origin),
            "review_state": "pending_independent_semantic_review"})
        if readable:
            question = {"qid": qid, "question": text, "origin": origin}
            if isinstance(raw, dict) and "reference_proposal" in raw:
                question["reference_proposal"] = _copy(raw["reference_proposal"])
            questions.append(question)
    return candidates, questions


def _quantity(requested, candidates, questions):
    difference = len(questions) - requested
    return {"requested": requested, "returned_candidates": len(candidates),
            "readable_questions": len(questions), "unreadable_candidates": len(candidates) - len(questions),
            "difference": difference,
            "status": "exact" if difference == 0 else "shortfall" if difference < 0 else "excess"}


def _duplicates(questions):
    by_text = {}
    for question in questions:
        by_text.setdefault(question["question"], []).append(question["qid"])
    return [{"question": text, "qids": ids} for text, ids in by_text.items() if len(ids) > 1]


def propose_question_series(seed, design_intent, corpus, public_protocol, *, model: str,
                            batch_specs, chat_json: Callable, max_calls: int,
                            max_input_chars: int, max_tokens: int,
                            record: Callable | None = None, on_batch: Callable | None = None) -> dict:
    """Propose serial batches; for this BC run callers request four batches of six.

    ``batch_specs`` is an ordered list of objects with ``focus`` (free text) and
    ``count`` (positive integer). Extra spec data are retained as author context.
    Titles remain hidden, matching the current solver view. Earlier full question
    texts are author-only context. Exact text duplicates are recorded, never
    removed; semantic equivalence is not decided here.

    ``record(event)`` receives copied before/after call records plus series events.
    ``on_batch(snapshot)`` runs after each completed or skipped batch, receiving
    ``batch_index`` (zero-based), the untouched ``batch_report``, accumulated
    quantities, and the series binding. Callback failures propagate; neither
    callback can mutate internal state. Budgets never trigger truncation/filling.
    """
    specs = _batch_specs(batch_specs)
    _integer(max_calls, "max_calls", zero=True)
    _integer(max_input_chars, "max_input_chars")
    _integer(max_tokens, "max_tokens")
    # Validate caller inputs before any trace callback or model call, then bind
    # detached copies so caller/callback mutation cannot change later batches.
    frozen = _copy({"seed": seed, "design_intent": design_intent, "corpus": corpus,
                    "public_protocol": public_protocol, "batch_specs": specs})
    initial = prepare_proposals(frozen["corpus"], frozen["public_protocol"],
        frozen["design_intent"], seed=frozen["seed"], model=model, count=specs[0]["count"])
    binding = {
        "version": VERSION, "proposal_version": initial["version"],
        "proposal_prompt_hash": initial["binding"]["prompt_hash"],
        "proposal_implementation_hash": initial["binding"]["implementation_hash"],
        "implementation_hash": digest(Path(__file__).read_text(encoding="utf-8")),
        "author_context_hash": digest(AUTHOR_CONTEXT), "frozen_inputs_hash": digest(frozen),
        "corpus_hash": initial["binding"]["corpus_hash"],
        "protocol_hash": initial["binding"]["protocol_hash"],
        "visible_view": initial["binding"]["visible_view"],
        "citation_policy": initial["binding"]["citation_policy"], "model": model,
        "batch_specs_hash": digest(specs),
        "budget": {"max_calls": max_calls, "max_input_chars": max_input_chars, "max_tokens": max_tokens},
    }
    binding_hash = digest(binding)
    requested = sum(spec["count"] for spec in specs)
    report = {"version": VERSION, "mode": "executed", "result_scope": "research_only",
        "publication_effect": "none", "review_state": "pending_independent_semantic_review",
        "binding": binding, "binding_hash": binding_hash, "frozen_inputs": frozen,
        "documents": initial["documents"], "source_map": initial["source_map"],
        "public_protocol": _copy(frozen["public_protocol"]), "batch_reports": [],
        "candidates": [], "questions": [], "records": [], "calls_used": 0,
        "quantity": _quantity(requested, [], []), "batch_quantities": [],
        "duplicates": {"policy": "exact_text_identity_only_no_filter",
                       "semantic_duplicate_review": "pending", "groups": []},
        "execution": {"status": "running", "processed_batches": 0,
                      "requested_batches": len(specs), "batch_status_counts": {}}}

    def emit(event):
        event = _copy(event)
        report["records"].append(event)
        if record is not None:
            record(deepcopy(event))

    emit({"event": "series_started", "binding": binding, "frozen_inputs": frozen})
    for index, spec in enumerate(specs):
        intent = {"intent": _copy(frozen["design_intent"]), "author_context": AUTHOR_CONTEXT,
            "series_context": {"batch_index": index, "batch_count": len(specs),
                "focus": spec["focus"], "batch_spec": _copy(spec),
                "previous_questions": [{"qid": q["qid"], "question": q["question"]}
                                       for q in report["questions"]]}}

        def batch_record(event):
            emit({**event, "series_binding_hash": binding_hash, "batch_index": index})

        batch = propose_questions(_copy(frozen["corpus"]), _copy(frozen["public_protocol"]), intent,
            seed=_copy(frozen["seed"]), model=model, count=spec["count"], chat_json=chat_json,
            max_calls=1 if report["calls_used"] < max_calls else 0,
            max_input_chars=max_input_chars, max_tokens=max_tokens, record=batch_record)
        report["calls_used"] += batch["calls_used"]
        report["batch_reports"].append(batch)
        candidates, questions = _collect_batch(batch, index, binding_hash)
        report["candidates"].extend(candidates)
        report["questions"].extend(questions)
        quantity = {"batch_index": index, **_quantity(spec["count"], candidates, questions)}
        report["batch_quantities"].append(quantity)
        report["quantity"] = _quantity(requested, report["candidates"], report["questions"])
        report["duplicates"]["groups"] = _duplicates(report["questions"])
        state = batch["execution"]["status"]
        counts = report["execution"]["batch_status_counts"]
        counts[state] = counts.get(state, 0) + 1
        report["execution"]["processed_batches"] += 1
        emit({"event": "batch_collected", "batch_index": index, "series_binding_hash": binding_hash,
              "calls_used": report["calls_used"], "quantity": quantity,
              "retained_qids": [q["qid"] for q in questions], "execution": batch["execution"]})
        if on_batch is not None:
            on_batch(deepcopy({"batch_index": index, "batch_report": batch,
                "series_binding": binding, "calls_used": report["calls_used"],
                "series_quantity": report["quantity"], "batch_quantity": quantity}))
    states = set(report["execution"]["batch_status_counts"])
    report["execution"]["status"] = ("ok" if states == {"ok"} else
        "invalid_output" if states <= {"ok", "invalid_output"} else "incomplete")
    report["series_output_hash"] = digest({"binding_hash": binding_hash,
        "batches": [{"binding": b["binding"], "raw_output": b["raw_output"],
                     "execution": b["execution"]} for b in report["batch_reports"]]})
    emit({"event": "series_finished", "series_binding_hash": binding_hash,
          "series_output_hash": report["series_output_hash"], "calls_used": report["calls_used"],
          "execution": report["execution"], "quantity": report["quantity"]})
    return report
