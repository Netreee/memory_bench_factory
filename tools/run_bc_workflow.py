"""Persist one bounded author/edit/independent-review research cycle.

Create a case with plan.json, original_snapshot.json and development_issues.json.
Each phase is exclusive and immutable; a failed phase requires an explicit new
case/lineage, never hidden resampling. No provider/config imports during dry run.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_bc_case import read, save, sha, provider
from pipeline.agent_editing import (propose_material_revision, review_materials,
                                   propose_question_revisions, AuditRecordError)
from pipeline.quality_workflow import snapshot, fresh_review, change_impact, make_receipt

SOURCES = ["tools/run_bc_workflow.py", "pipeline/agent_editing.py", "pipeline/quality_workflow.py",
           "pipeline/semantic_review.py", "eval/semantic_judge.py", "eval/grading.py",
           "eval/provenance.py", "eval/reassessment.py", "eval/multi_system.py",
           "tools/run_bc_case.py", "config.py", "llm_trace.py"]
PHASES = ("material_edit", "material_review", "question_edit", "final_review", "receipt")


def checked_snapshot(path):
    saved = read(path)
    actual = snapshot(saved["questions"], saved["corpus"], saved["protocol"])
    for field in ("version", "snapshot_id", "public_context_hash", "identities"):
        if saved.get(field) != actual[field]:
            raise ValueError("Snapshot content/binding mismatch: " + field)
    return saved


def run(case, phase, *, execute=False, workers=3):
    case = Path(case).resolve()
    plan = read(case / "plan.json")
    if plan.get("result_scope") != "research_only":
        raise ValueError("Explicit research_only plan required")
    original = checked_snapshot(case / "original_snapshot.json")
    issues = read(case / "development_issues.json")
    if phase not in PHASES: raise ValueError("Unknown phase")
    if type(workers) is not int or not 1 <= workers <= 4: raise ValueError("Invalid workers")
    inputs = [case / n for n in ("plan.json", "original_snapshot.json", "development_issues.json")]
    material = original
    current = None
    if phase != "material_edit":
        material = checked_snapshot(case / "material_snapshot.json")
        inputs.append(case / "material_snapshot.json")
    if phase in {"final_review", "receipt"}:
        current = checked_snapshot(case / "current_snapshot.json")
        inputs.append(case / "current_snapshot.json")
    if phase == "receipt":
        inputs.extend([case / "final_review/report.json", case / "question_edit/report.json"])
        if (case / "acceptance_decision.json").exists(): inputs.append(case / "acceptance_decision.json")
    # Source files must match the frozen plan even between phases.
    if set(plan["source_sha256"]) != set(SOURCES):
        raise ValueError("Plan must freeze the complete workflow source set")
    for name, expected in plan["source_sha256"].items():
        if sha(ROOT / name) != expected:
            raise ValueError("Frozen implementation changed; create an explicit derived case: " + name)
    max_calls = plan["budgets"].get(phase, 0)
    if type(max_calls) is not int or max_calls < 0: raise ValueError("Invalid phase budget")
    if not execute:
        return {"phase": phase, "ready": True, "calls_permitted": 0,
                "planned_max_calls": max_calls, "result_scope": "research_only"}
    directory = case / phase
    directory.mkdir(exist_ok=False)
    save(directory / "manifest.json", {"phase": phase, "started_unix": time.time(),
        "result_scope": "research_only", "input_sha256": {str(p.relative_to(case)): sha(p) for p in inputs},
        "source_sha256": {s: sha(ROOT / s) for s in SOURCES}, "max_calls": max_calls,
        "automatic_retry": False, "publication_effect": "none"})
    if phase == "receipt":
        decision = read(case / "acceptance_decision.json") if (case / "acceptance_decision.json").exists() else {}
        edited = read(case / "question_edit/report.json")
        dispositions = {d["qid"]: {"action": d["action"], "reason": d["reason"]} for d in edited["decisions"]}
        report = make_receipt(original, current, read(case / "final_review/report.json"),
            material_ready=decision.get("material_ready", False), calibration=decision.get("calibration"),
            dispositions=dispositions)
        report["acceptance_decision"] = decision
        save(directory / "report.json", report)
        return report
    call, record, clean = provider(directory)
    attempts = 0
    # One shared lock enforces the phase budget even for parallel readers.
    import threading
    lock = threading.Lock()
    def bounded_call(step, messages, **params):
        nonlocal attempts
        with lock:
            if attempts >= max_calls: raise RuntimeError("Phase provider attempt budget exhausted")
            attempts += 1
        return call(step, messages, **params)
    common = dict(chat_json=bounded_call, record=record, max_input_chars=plan["max_input_chars"])
    try:
        if phase == "material_edit":
            report = propose_material_revision(original["corpus"], original["protocol"], issues["material_issues"],
                model=plan["editor_model"], max_calls=min(1, max_calls), max_tokens=plan["material_edit_max_tokens"], **common)
            save(directory / "report.json", clean(report))
            if not report["proposal_ready"] or report["proposal_state"] == "unresolved":
                raise RuntimeError("Material revision not ready; raw response preserved")
            corpus = report.get("proposed_corpus") or original["corpus"]
            save(case / "material_snapshot.json", snapshot(original["questions"], corpus,
                 original["protocol"], parent=original))
        elif phase == "material_review":
            report = review_materials(material["corpus"], material["protocol"], model=plan["reviewer_model"],
                max_calls=min(1, max_calls), max_tokens=plan["review_max_tokens"], **common)
            save(directory / "report.json", clean(report))
        elif phase == "question_edit":
            size = plan["question_batch_size"]
            if type(size) is not int or size < 1: raise ValueError("Invalid batch size")
            parts, decisions, proposed, variants = [], [], [], []
            for start in range(0, len(material["questions"]), size):
                batch = material["questions"][start:start+size]
                qids = {q["qid"] for q in batch}
                selected_issues = [i for i in issues["question_issues"] if "qid" not in i or i["qid"] in qids]
                part = propose_question_revisions(batch, material["corpus"], material["protocol"], selected_issues,
                    model=plan["editor_model"], max_calls=1 if attempts < max_calls else 0,
                    max_tokens=plan["question_edit_max_tokens"], **common)
                save(directory / f"batch_{len(parts)+1:02d}.json", clean(part))
                parts.append(part)
                if not part["proposal_ready"]:
                    save(directory / "report.json", {"status": "incomplete", "parts": parts,
                        "calls_used": attempts, "raw_preserved": True})
                    raise RuntimeError("Question editing incomplete; no partial current snapshot exported")
                decisions.extend(part["decisions"])
                proposed.extend(part["proposed_questions"])
                variants.extend(part["variants"])
                print(json.dumps({"phase": phase, "batch": len(parts), "quantity": part["quantity"]}), flush=True)
            report = {"status": "proposed", "calls_used": attempts, "decisions": decisions,
                      "variants": variants, "new_question_count": 0, "parts": len(parts)}
            save(directory / "report.json", clean(report))
            current = snapshot(proposed, material["corpus"], material["protocol"], parent=original)
            save(case / "current_snapshot.json", current)
            save(case / "change_impact.json", change_impact(original, current))
        else:
            def checkpoint(index, part):
                save(directory / f"item_{index+1:03d}.json", clean(part))
                item = part["items"][0]
                print(json.dumps({"phase": phase, "index": index+1, "qid": item["source_qid"],
                    "state": item["review_state"], "reference_status": item.get("reference_status")}), flush=True)
            report = fresh_review(current, reader_model=plan["reader_model"], reviewer_model=plan["reviewer_model"],
                max_calls=max_calls, max_tokens=plan["review_max_tokens"], workers=workers, on_item=checkpoint, **common)
            save(directory / "report.json", clean(report))
    except AuditRecordError as exc:
        save(directory / "audit_failure.json", clean(exc.report))
        raise
    finally:
        save(directory / "attempt_count.json", {"actual_provider_attempts": attempts, "max_calls": max_calls})
    return {"phase": phase, "provider_attempts": attempts, "result_scope": "research_only"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--case", type=Path, required=True)
    p.add_argument("--phase", choices=PHASES, required=True)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--workers", type=int, default=3)
    args = p.parse_args()
    case = args.case.resolve()
    historical = (ROOT / "output/runs").resolve()
    if historical == case or historical in case.parents: p.error("Cannot write historical run directories")
    print(json.dumps(run(case, args.phase, execute=args.execute, workers=args.workers), ensure_ascii=False))


if __name__ == "__main__": main()
