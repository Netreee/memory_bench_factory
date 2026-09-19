"""Read-only, offline acceptance audit for a completed seeded generation run.

No model clients or API calls. Optional --output writes only the audit report.
Question association is a diagnostic, not a proof that a mechanism is tested.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.grounding import run_grounding
from pipeline.seed_pack import seed_contract, seed_digest, validate_seed_pack
from pipeline.seed_world import seed_world_report
from pipeline.world_state import WorldState


REQUIRED_ARTIFACTS = (
    "manifest.json", "00_seed_pack.json", "00_input.json", "01_whitepaper.json",
    "01_seed_audit.json", "02_world.json", "02_seed_audit.json", "03_orders.json",
    "03_well_posed_report.json", "04_questions.json", "05_corpus.json",
    "06_grounded_questions.json", "06_grounding_report.json",
)
STAGES = ("input", "whitepaper", "world", "orders", "well_posed", "questions", "corpus", "grounding")


def event_visible(event: dict, content: str) -> bool:
    """Same mechanical participant/effect standard as render._event_is_narrated.

    Kept local to avoid importing model configuration from pipeline.render.
    It verifies exact tokens together, not natural-language causal meaning.
    """
    participants = {str(value) for value in (event.get("participants") or {}).values() if value}
    effects = event.get("effects") or []
    return bool(participants and effects and all(name in content for name in participants)
                and all(effect.get("entity") and effect.get("field")
                        and effect.get("set", effect.get("value")) is not None
                        and str(effect["entity"]) in content
                        and str(effect["field"]) in content
                        and str(effect.get("set", effect.get("value"))) in content
                        for effect in effects))


def scalar_values(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from scalar_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from scalar_values(item)
    elif value is not None:
        yield str(value)


def audit(run_dir: Path) -> dict:
    missing = [name for name in REQUIRED_ARTIFACTS if not (run_dir / name).is_file()]
    result = {"run_dir": str(run_dir.resolve()), "passed": False,
              "missing_artifacts": missing, "issues": [], "warnings": []}
    if missing:
        result["issues"].append("Full generation has not published every required artifact.")
        return result

    def read(name):
        return json.loads((run_dir / name).read_text(encoding="utf-8"))

    manifest, pack = read("manifest.json"), validate_seed_pack(read("00_seed_pack.json"))
    if pack["schema_version"] == 2:
        from types import SimpleNamespace
        from pipeline.seed_run import validate_seed_identity
        from pipeline.seed_lineage import seed_lineage_report
        result["seed_lineage"] = seed_lineage_report(run_dir)
        try:
            validate_seed_identity(SimpleNamespace(manifest=manifest,
                has=lambda name: (run_dir / name).is_file(), read=read), read("01_whitepaper.json"))
        except ValueError as exc:
            result["issues"].append(str(exc))
    wp, world = read("01_whitepaper.json"), read("02_world.json")
    corpus, questions = read("05_corpus.json"), read("04_questions.json")
    final = read("06_grounded_questions.json")
    issues, warnings = result["issues"], result["warnings"]
    digest = seed_digest(pack)
    input_seed = read("00_input.json").get("seed", {})
    identity_ok = (manifest.get("config", {}).get("seed_pack_digest") == digest
                   and input_seed.get("digest") == digest
                   and wp.get("seed_contract") == seed_contract(pack)
                   and all(q.get("seed") == input_seed for q in questions + final))
    if not identity_ok:
        issues.append("Seed identity differs across frozen input, whitepaper or questions.")
    result["seed_identity"] = {"passed": identity_ok, "seed_id": pack["seed_id"], "digest": digest}
    result["stage_statuses"] = {name: manifest.get("stages", {}).get(name, {}).get("status") for name in STAGES}
    if any(status != "succeeded" for status in result["stage_statuses"].values()):
        issues.append("Not every generation stage has succeeded.")
    ws = WorldState.from_dict(world)
    result["seed_world"] = seed_world_report(wp, ws)
    if not result["seed_world"]["passed"]:
        issues.extend(result["seed_world"]["issues"])
    if not read("01_seed_audit.json").get("passed") or not read("02_seed_audit.json").get("passed"):
        issues.append("A published seed audit failed.")

    sessions = corpus.get("corpus", corpus).get("sessions", [])
    by_session = {session["session_id"]: session.get("docs", []) for session in sessions}
    docs = [doc for session in sessions for doc in session.get("docs", [])]
    doc_ids = [doc.get("doc_id") for doc in docs]
    doc_index = {doc.get("doc_id"): (session["session_id"], doc)
                 for session in sessions for doc in session.get("docs", [])}
    if not all(doc_ids) or len(doc_ids) != len(set(doc_ids)):
        issues.append("Corpus document IDs are empty or duplicated.")
    expected_sessions = set(range(int(world["n_sessions"])))
    if set(by_session) != expected_sessions or set(corpus.get("done_weeks", [])) != expected_sessions:
        issues.append("Published corpus does not cover all canonical sessions.")
    result["size"] = {"entities": len(world.get("entities", {})), "sessions": len(sessions),
                      "documents": len(docs), "signal_documents": sum(not doc.get("is_filler") for doc in docs),
                      "filler_documents": sum(bool(doc.get("is_filler")) for doc in docs),
                      "characters": sum(len(doc.get("content", "")) for doc in docs),
                      "orders": len(read("03_orders.json")), "questions": len(questions),
                      "grounded_questions": len(final)}
    if not final:
        issues.append("Final grounded question set is empty.")
    invalid_evidence = []
    for index, question in enumerate(final, 1):
        evidence = question.get("evidence_doc_ids") or []
        window = question.get("evidence_sessions") or []
        if not evidence or any(doc_id not in doc_index or doc_index[doc_id][0] not in window
                               or doc_index[doc_id][1].get("is_filler") for doc_id in evidence):
            invalid_evidence.append(index)
    result["invalid_question_evidence_indexes"] = invalid_evidence
    if invalid_evidence:
        issues.append("Some final questions have missing, filler, or out-of-window evidence IDs.")
    recomputed, grounding = run_grounding(questions, corpus)
    grounding_same = recomputed == final and grounding == read("06_grounding_report.json")
    result["grounding_replay_identical"] = grounding_same
    result["grounding"] = grounding
    if not grounding_same:
        issues.append("Offline grounding replay differs from the published final artifacts.")

    required_event_types = {item["id"] for item in pack["blueprint_requirements"].get("event_types", [])}
    event_reports = []
    for event in world.get("events", []):
        if event.get("type") not in required_event_types:
            continue
        witnesses = [doc["doc_id"] for doc in by_session.get(event["session"], [])
                     if not doc.get("is_filler") and event_visible(event, doc.get("content", ""))]
        effects = {(effect.get("entity"), effect.get("field"), str(effect.get("set", effect.get("value"))))
                   for effect in event.get("effects", [])}
        adjacent, matching_gold = [], []
        for index, question in enumerate(final, 1):
            if question.get("line") == "L6_refusal" or event["session"] not in question.get("evidence_sessions", []):
                continue
            matches = [value for entity, field, value in effects
                       if question.get("entity") == entity and question.get("field") == field]
            if matches:
                adjacent.append(index)
                if any(value in set(scalar_values(question.get("gt"))) for value in matches):
                    matching_gold.append(index)
        event_reports.append({"event_id": event["id"], "type": event["type"], "session": event["session"],
                              "participants": event.get("participants", {}), "effects": event.get("effects", []),
                              "corpus_doc_ids": witnesses, "effect_field_question_indexes": adjacent,
                              "effect_value_in_gold_question_indexes": matching_gold})
        if not witnesses:
            issues.append(f"Required event {event['id']} has no complete same-session signal document.")
        if not matching_gold:
            warnings.append(f"Event {event['id']} has no direct effect-field question with its value in gold; inspect indirect coverage.")
    result["event_coverage"] = event_reports
    result["question_association_scope"] = ("Indexes are 1-based within 06_grounded_questions.json. "
        "Effect-field/time-window association and exact gold overlap are diagnostics only; "
        "they do not prove that a question tests causal dependence or version adoption.")
    result["questions_by_line"] = dict(Counter(q.get("line") for q in final))
    result["questions_by_capability"] = dict(Counter(q.get("capability") for q in final))
    if pack.get("family") == "insurance_annual_review":
        source_client_checks = []
        for event in world.get("events", []):
            if event.get("type") != "adoption_revised":
                continue
            roles, session = event.get("participants", {}), event["session"]
            source_timeline = ws.timeline(roles.get("source"), "来源客户")
            analysis_timeline = ws.timeline(roles.get("analysis"), "分析客户")
            source_client = source_timeline.value_at_session(session) if source_timeline else None
            analysis_client = analysis_timeline.value_at_session(session) if analysis_timeline else None
            has_source_client = source_client in world.get("entities", {})
            has_analysis_client = analysis_client in world.get("entities", {})
            same_client = has_source_client and has_analysis_client and source_client == analysis_client
            status = ("passed" if same_client else "missing_relation"
                      if not (has_source_client and has_analysis_client) else "client_mismatch")
            related_question_indexes = [index for index, question in enumerate(final, 1)
                                        if question.get("entity") == roles.get("analysis")
                                        and question.get("line") != "L6_refusal"
                                        and ("采用来源" in question.get("field", "")
                                             or "来源客户" in question.get("field", ""))]
            source_client_checks.append({"event_id": event["id"], "source": roles.get("source"),
                                         "analysis": roles.get("analysis"), "session": session,
                                         "source_client": source_client, "analysis_client": analysis_client,
                                         "passed": same_client, "status": status,
                                         "related_adoption_question_indexes": related_question_indexes})
            if not same_client:
                issues.append(f"Insurance event {event['id']} source/analysis client check: {status}.")
        result["insurance_same_client_checks"] = source_client_checks
        changes = []
        for name, fields in world.get("entities", {}).items():
            values = [op.get("value") for op in fields.get("采用来源", [])
                      if op.get("op") in ("SET", "UPDATE") and op.get("value")]
            if len(set(values)) >= 2:
                changes.append({"analysis": name, "adopted_sources_in_order": values})
        result["actual_adopted_source_switches"] = changes
        if not changes:
            warnings.append("No analysis actually switches between two adopted sources. The current seed requires an adoption/recheck chain, not an old-to-new switch.")
    result["semantic_review_required"] = [
        "Company/year in source and analysis titles must agree with canonical linked entities and periods.",
        "Manually check business meaning of adoption/recheck states and same-client source selection.",
        "This audit does not validate financial formulas, time-cutoff semantics, or benchmark discriminative power.",
    ]
    result["passed"] = not issues
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.run_dir)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
