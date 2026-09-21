"""Versioned release gate shared by generation, evaluation and UI.

A completed process and a valid benchmark are distinct states. A release receipt
is valid only for its exact input artifacts and quality implementation. Legacy
or stale artifacts stay inspectable through an explicit research override.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path

VERSION = 1
RELEASE_ARTIFACT = "07_release.json"
INPUTS = ("01_whitepaper.json", "02_world.json", "04_questions.json",
          "05_corpus.json", "06_grounded_questions.json", "00_about.json")
ROOT = Path(__file__).resolve().parent.parent


class ReleaseError(ValueError):
    pass


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def implementation_fingerprint() -> str:
    paths = [ROOT / "pipeline" / name for name in (
        "quality.py", "corpus_contract.py", "question_contract.py", "value_types.py",
        "world_state.py", "well_posed.py", "grounding.py", "grounding_review.py",
        "semantic_review.py", "reference_audit.py", "reference_locations.py",
        "question_wording.py", "render.py", "seed_world.py", "seed_pack.py", "seed_v2.py", "seed_run.py",
        "world_semantics.py", "world_context.py", "disclosure.py", "disclosure_batches.py",
        "process_proposals.py", "process_batches.py", "paged_read.py", "order_warning.py")]
    paths += sorted((ROOT / "pipeline/lines").glob("*.py"))
    paths += [ROOT / "eval" / name for name in ("judge.py", "grading.py", "semantic_judge.py",
                                               "answer_task_review.py", "provenance.py")]
    values = [(p.relative_to(ROOT).as_posix(), file_hash(p) if p.is_file() else "missing") for p in paths]
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def _read(directory, name):
    return json.loads((Path(directory) / name).read_text(encoding="utf-8"))


def _release_inputs(directory):
    from pipeline.grounding_review import enabled, REVIEW_ARTIFACT
    from pipeline import world_semantics
    wp_path = Path(directory) / "01_whitepaper.json"
    names = list(INPUTS)
    for warning_name in ("02_world_review_warning.json", "02_world_repair_failure.json",
                         "03_orders_warning.json",
                         "04_questions_warning.json",
                         "05_corpus_warning.json",
                         "05_corpus_candidate.json", "06_production_warning.json"):
        if (Path(directory) / warning_name).is_file():
            names.append(warning_name)
    if (Path(directory) / "03_orders_warning.json").is_file():
        for evidence_name in ("03_process_proposals.json", "03_orders_warning_resolution.json"):
            if (Path(directory) / evidence_name).is_file():
                names.append(evidence_name)
    if wp_path.exists():
        wp = _read(directory, "01_whitepaper.json")
        manifest = _read(directory, "manifest.json") if (Path(directory) / "manifest.json").is_file() else {}
        if enabled(wp):
            names.append(REVIEW_ARTIFACT)
        if world_semantics.enabled(wp, manifest.get("config", {})):
            names += [world_semantics.REVIEW_ARTIFACT, "00_input.json"]
        if (wp.get("seed_contract") or {}).get("schema_version") == 2:
            from pipeline.seed_run import SEED_ARTIFACT, AUDIT_ARTIFACT, GENERATION_ARTIFACT
            names += [SEED_ARTIFACT, AUDIT_ARTIFACT, GENERATION_ARTIFACT, "00_input.json"]
    return tuple(dict.fromkeys(names))


def _delivery_contract(directory) -> dict:
    path = Path(directory) / "manifest.json"
    manifest = _read(directory, "manifest.json") if path.exists() else {}
    target = manifest.get("algo", {}).get("targetspec", {})
    result = {"min_questions": target.get("min_questions", 0),
              "per_line_min": target.get("per_line_min", {}),
              "inherited_research_only": (manifest.get("release_policy") or {}).get("inherited_research_only", False)}
    result["world_semantic_review"] = manifest.get("config", {}).get("world_semantic_review", False)
    wp_path = Path(directory) / "01_whitepaper.json"
    if wp_path.is_file() and (_read(directory, "01_whitepaper.json").get("seed_contract") or {}).get("schema_version") == 2:
        result["seed_input_identity"] = {key: manifest.get("config", {}).get(key)
                                          for key in ("seed_id", "seed_pack_digest")}
    if (type(result["min_questions"]) is not int or result["min_questions"] < 0
            or type(result["inherited_research_only"]) is not bool
            or type(result["world_semantic_review"]) is not bool
            or not isinstance(result["per_line_min"], dict)
            or any(not isinstance(k, str) or type(v) is not int or v < 0
                   for k, v in result["per_line_min"].items())):
        raise ValueError("Invalid delivery target contract")
    return result


def _manual_rejection(directory) -> bool:
    manifest_path = Path(directory) / "manifest.json"
    if manifest_path.exists():
        try:
            if _read(directory, "manifest.json").get("status") in ("manual_failed", "rejected"):
                return True
        except (ValueError, OSError, AttributeError):
            return True
    path = Path(directory) / "08_semantic_review.json"
    if not path.exists():
        return False
    try:
        review = json.loads(path.read_text(encoding="utf-8"))
        return review.get("review_status") in ("manual_failed", "failed", "rejected")
    except (ValueError, OSError, AttributeError):
        return True  # unreadable reviewer result cannot silently certify a release


def _scoring_contract_supported(question, whitepaper, protocol):
    """Admit an explicit scoring route, not a passed semantic judgement.

    The normal contract/world checks and current isolated public review remain
    mandatory later in evaluate_release. Legacy judgeability stays unchanged.
    """
    from eval.grading import requires_semantic_grading
    from eval.judge import is_judgeable
    if not requires_semantic_grading(question):
        return is_judgeable(question)
    from eval.answer_task_review import POLICY_VERSION
    from eval.provenance import protocol_scoring_policy
    from pipeline.reference_audit import validate_reference_proposal
    qc = whitepaper.get("quality_contract") or {}
    contract, aux = question.get("question_contract") or {}, question.get("aux") or {}
    if not all(isinstance(value, dict) for value in (qc, contract, aux)):
        return False
    try:
        validate_reference_proposal(question.get("reference_proposal"))
        public_policy = protocol_scoring_policy(protocol)
    except (ValueError, TypeError):
        return False
    return (qc.get("scoring_policy") == public_policy == POLICY_VERSION
            and qc.get("public_semantic_review") is True
            and qc.get("isolated_reference_audit") is True
            and question.get("line") == "L3_process"
            and aux.get("scorer") == "semantic"
            and aux.get("gt_scope") == "canonical_witness_not_semantic_proof"
            and contract.get("scoring_policy") == POLICY_VERSION
            and contract.get("scoring_scope") == "task_with_supporting_reasons"
            and contract.get("answer_kind") == "structured"
            and contract.get("render_policy") == "semantic_review"
            and contract.get("reference_authority") == "proposed_not_mechanically_proven"
            and contract.get("gold") == question["reference_proposal"]["answer"]
            and contract.get("reference_proposal") == question["reference_proposal"]
            and contract.get("canonical_witness") == question.get("gt")
            and (contract.get("parameters") or {}).get("process") == aux.get("process"))


def evaluate_release(directory) -> dict:
    """Pure read-only evaluation. The factory writes the returned receipt."""
    from pipeline.corpus_contract import validate_corpus
    from pipeline.question_contract import validate_question, build_question_contract, bind_question_world
    from pipeline.world_state import WorldState
    from pipeline.well_posed import run_well_posed
    from pipeline.grounding import run_grounding
    from pipeline.seed_world import seed_world_report
    from eval.judge import is_judgeable
    from pipeline.lines import line_for
    from pipeline.value_types import values_equal

    directory = Path(directory)
    issues, warnings, hashes, checks = [], [], {}, {}
    for name in _release_inputs(directory):
        path = directory / name
        if not path.is_file():
            issues.append({"code": "missing_release_input", "artifact": name})
        else:
            hashes[name] = file_hash(path)
    report = {"version": VERSION, "status": "failed", "eligible": False,
              "scope": ["question_contract", "world_answer_consistency", "independent_date_extrema",
                        "corpus_supportedness", "evidence_references", "current_corpus_grounding",
                        "declared_delivery_targets"],
              "inputs": hashes, "implementation_fingerprint": implementation_fingerprint(),
              "checks": checks, "issues": issues, "warnings": warnings,
              "limitations": ["Model-assisted semantic review can still miss or misclassify claims.",
                              "Process witness consistency is structural; its natural reference depends on the recorded public semantic review.",
                              "This gate does not certify business rules absent from the seed contract.",
                              "Candidate document count is not minimal necessary evidence or difficulty."]}
    if issues:
        return report
    try:
        wp = _read(directory, "01_whitepaper.json")
        manifest = _read(directory, "manifest.json") if (directory / "manifest.json").is_file() else {}
        if (wp.get("seed_contract") or {}).get("schema_version") == 2:
            from types import SimpleNamespace
            from pipeline.seed_run import validate_seed_identity
            view = SimpleNamespace(manifest=manifest, has=lambda name: (directory / name).is_file(),
                                   read=lambda name: _read(directory, name))
            validate_seed_identity(view, wp)
            checks["seed_input_identity"] = {"status": "passed", "schema_version": 2}
            report["scope"].append("frozen_seed_extraction_and_generation_context")
            from pipeline import world_semantics
            if ((wp.get("quality_contract") or {}).get("public_disclosure") is not True
                    or not world_semantics.enabled(wp, manifest.get("config", {}))):
                issues.append({"code": "seed_v2_requires_world_review_and_disclosure"})
        if (manifest.get("release_policy") or {}).get("inherited_research_only"):
            issues.append({"code": "unverified_source_derivation"})
        report["delivery_contract"] = _delivery_contract(directory)
        ws = WorldState.from_dict(_read(directory, "02_world.json"))
        order_resolution = None
        if (directory / "03_orders_warning.json").is_file():
            from pipeline import order_warning
            order_errors = []
            try:
                warning = _read(directory, order_warning.WARNING_ARTIFACT)
                proposal = _read(directory, order_warning.PROPOSAL_ARTIFACT)
                order_resolution = _read(directory, order_warning.RESOLUTION_ARTIFACT)
                order_errors = order_warning.validate_resolution(
                    order_resolution, wp, ws.to_dict(), _read(directory, "04_questions.json"),
                    warning, proposal)
            except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
                order_errors = [{"code": "invalid_order_warning_resolution",
                                 "message": f"{type(exc).__name__}: {exc}"}]
            checks["order_warning_scope"] = {
                "status": "passed" if not order_errors else "failed",
                "issues": order_errors,
                "affected_lines": (order_resolution or {}).get("affected_lines", []),
                "excluded_qids": (order_resolution or {}).get("excluded_qids", []),
            }
            if order_errors:
                issues.append({"code": "order_generation_warning",
                               "artifact": "03_orders_warning.json", "details": order_errors})
            else:
                warnings.append({"code": "order_warning_scoped_exclusion",
                                 "lines": order_resolution["affected_lines"],
                                 "excluded_questions": len(order_resolution["excluded_qids"])})
        if (directory / "04_questions_warning.json").is_file():
            issues.append({"code": "question_generation_warning",
                           "artifact": "04_questions_warning.json"})
        if (directory / "05_corpus_warning.json").is_file():
            issues.append({"code": "corpus_generation_warning",
                           "artifact": "05_corpus_warning.json"})
        if (directory / "06_production_warning.json").is_file():
            issues.append({"code": "production_execution_warning",
                           "artifact": "06_production_warning.json"})
        from pipeline import world_semantics
        if world_semantics.enabled(wp, manifest.get("config", {})):
            world_review = _read(directory, world_semantics.REVIEW_ARTIFACT)
            world_errors = world_semantics.validate_review(world_review, wp, ws,
                task_input=_read(directory, "00_input.json"))
            checks["world_semantics"] = {"status": world_review.get("status"), "issues": world_errors}
            report["scope"].append("world_business_semantics")
            if world_review.get("status") != "passed" or world_errors:
                issues.append({"code": "world_semantic_review_not_current", "details": world_errors})
        elif (wp.get("quality_contract") or {}).get("public_disclosure") is True:
            issues.append({"code": "disclosure_requires_world_semantic_review"})
        source = _read(directory, "04_questions.json")
        questions = _read(directory, "06_grounded_questions.json")
        corpus = _read(directory, "05_corpus.json")
        from pipeline.grounding_review import enabled, candidates_with_evidence, selection, validate_delivery, REVIEW_ARTIFACT
        from eval.provenance import public_protocol, protocol_scoring_policy
        semantic_enabled = enabled(wp)
        protocol = public_protocol(_read(directory, "00_about.json"))
        expected_policy = (wp.get("quality_contract") or {}).get("scoring_policy")
        if protocol_scoring_policy(protocol) != expected_policy:
            issues.append({"code": "public_scoring_policy_mismatch"})
        if not isinstance(questions, list) or not questions:
            raise ValueError("Final question set is empty or not a list")
        if not isinstance(source, list):
            raise ValueError("Question source is not a list")
        if _manual_rejection(directory):
            issues.append({"code": "explicit_semantic_rejection"})
        seed = seed_world_report(wp, ws)
        checks["seed_structure"] = seed
        if not seed.get("passed", False):
            issues.append({"code": "seed_contract_failed", "details": seed.get("issues")})
        contracts = []
        seen_qids = set()
        source_by_id = {q.get("qid"): q for q in source if isinstance(q, dict) and q.get("qid")}
        for index, q in enumerate(questions, 1):
            qid = q.get("qid")
            if not _scoring_contract_supported(q, wp, protocol):
                issues.append({"code": "unsupported_scoring_contract", "index": index, "qid": qid})
            if not qid or qid in seen_qids:
                issues.append({"code": "missing_or_duplicate_qid", "index": index, "qid": qid})
            seen_qids.add(qid)
            if not q.get("question_contract"):
                contracts.append({"code": "missing_question_contract", "index": index})
            else:
                contracts.extend({"index": index, "qid": qid, **item} for item in validate_question(q))
                if q["question_contract"].get("render_policy") == "semantic_review":
                    from pipeline.question_wording import validate_wording
                    contracts.extend({"index": index, "qid": qid, **item} for item in validate_wording(q))
                trusted_order = bind_question_world(q, ws)
                trusted_wp = {**wp, "world_blueprint": ws.world_blueprint or wp.get("world_blueprint", {})}
                if (any(q.get(key) != trusted_order.get(key) for key in
                        ("entity_type", "answer_entity_type", "answer_field"))
                        or q["question_contract"] != build_question_contract(trusted_order, trusted_wp)):
                    contracts.append({"code": "question_contract_policy_mismatch", "index": index, "qid": qid})
                contract = q["question_contract"]
                if (contract.get("answer_kind") in ("value", "enum")
                        and contract.get("value_kind") in ("numeric", "number", "date", "calendar_date")):
                    production_line = line_for(q.get("line", ""))
                    expected = production_line.gt(ws, q) if production_line else None
                    expected = expected.get("value") if isinstance(expected, dict) else expected
                    supplied = q["gt"].get("value") if isinstance(q.get("gt"), dict) else q.get("gt")
                    if not values_equal(expected, supplied, contract.get("value_schema")):
                        issues.append({"code": "typed_gold_mismatch", "qid": qid})
            original = source_by_id.get(qid)
            if not original or any(original.get(key) != q.get(key) for key in (
                    "question", "gt", "aux", "question_contract", "question_validation", "capability", "entity", "field")):
                issues.append({"code": "question_source_mismatch", "index": index, "qid": qid})
            # Independent date oracle: do not reuse the production gt_mr implementation.
            if q.get("capability") == "MR":
                timeline = ws.timeline(q.get("entity"), q.get("field"))
                values = timeline.set_values() if timeline else []
                kind = q.get("question_contract", {}).get("value_kind")
                iso = all(isinstance(row[2], str) and len(row[2]) == 10
                          and row[2][4:5] == "-" and row[2][7:8] == "-" for row in values)
                if kind == "date" or (values and iso):
                    try:
                        compare = max if q.get("gt", {}).get("agg", "max") == "max" else min
                        expected = compare(date.fromisoformat(row[2]) for row in values).isoformat()
                        if q.get("gt", {}).get("value") != expected:
                            issues.append({"code": "independent_date_gold_mismatch", "qid": qid,
                                           "expected": expected, "actual": q.get("gt")})
                    except (ValueError, TypeError):
                        issues.append({"code": "invalid_date_series", "qid": qid})
        checks["question_contracts"] = {"n": len(questions), "issues": contracts}
        issues.extend(contracts)
        _, well_posed = run_well_posed(questions, ws)
        checks["world_answers"] = well_posed
        if well_posed.get("n_dropped"):
            issues.append({"code": "invalid_question_plan", "drops": well_posed.get("drops")})
        corpus_check = validate_corpus(ws, corpus)
        checks["corpus"] = corpus_check
        issues.extend(corpus_check["issues"])
        if semantic_enabled:
            review = _read(directory, REVIEW_ARTIFACT)
            isolated = (wp.get("quality_contract") or {}).get("isolated_reference_audit", False)
            if isolated != bool((review.get("binding") or {}).get("reference_auditor_model")):
                issues.append({"code": "semantic_review_method_mismatch"})
            candidates = candidates_with_evidence(source, corpus, isolated_reference=isolated)
            delivery = validate_delivery(candidates, corpus, protocol, review)
            checks["semantic_review_delivery"] = delivery
            if not delivery["delivery_safe"]:
                issues.extend(delivery["issues"])
            elif not delivery["execution_complete"]:
                warnings.append({"code": "parsed_format_failures_isolated_as_pending",
                                 "items": delivery["parsed_format_failures"]})
            expected_final, semantic_check = selection(candidates, review)
            if order_resolution is not None and not (checks.get("order_warning_scope") or {}).get("issues"):
                from pipeline import order_warning
                expected_final, _ = order_warning.apply_resolution(expected_final, semantic_check, order_resolution)
            derivation = manifest.get("derived_from") or {}
            if derivation.get("operation") == "filter_all_selected_systems_correct":
                inherited = derivation.get("semantic_review") or {}
                positions = {q.get("qid"): (i, q) for i, q in enumerate(expected_final)}
                indices = [positions.get(q.get("qid"), (-1, None))[0] for q in questions]
                if (inherited.get("artifact") != REVIEW_ARTIFACT
                        or inherited.get("sha256") != file_hash(directory / REVIEW_ARTIFACT)
                        or inherited.get("scope") != "unchanged_complete_source_review"
                        or inherited.get("source_final_count") != len(expected_final)
                        or inherited.get("selected_qids") != [q.get("qid") for q in questions]
                        or len(positions) != len(expected_final)
                        or any(i < 0 for i in indices) or indices != sorted(set(indices))
                        or any(positions.get(q.get("qid"), (-1, None))[1] != q for q in questions)):
                    issues.append({"code": "semantic_filter_derivation_mismatch"})
            elif expected_final != questions:
                issues.append({"code": "semantic_selection_mismatch"})
            checks["public_semantics"] = semantic_check
            if semantic_check["n_pending"]:
                warnings.append({"code": "unresolved_candidates_not_released", "n": semantic_check["n_pending"]})
            regrounded = candidates_with_evidence(questions, corpus)
            grounding_check = {"n_dropped": 0, "decision_basis": "public_semantic_review"}
        else:
            regrounded, grounding_check = run_grounding(questions, corpus)
        checks["grounding"] = grounding_check
        if grounding_check["n_dropped"]:
            issues.append({"code": "current_corpus_grounding_failed", "drops": grounding_check["drops"]})
        actual_evidence = {q.get("qid"): q.get("evidence_doc_ids", []) for q in regrounded}
        sessions = corpus.get("corpus", corpus).get("sessions", [])
        docs = {d.get("doc_id"): (s["session_id"], d)
                for s in sessions for d in s.get("docs", [])}
        for index, q in enumerate(questions, 1):
            ids = q.get("candidate_evidence_doc_ids", q.get("evidence_doc_ids", []))
            if (not ids and not semantic_enabled) or any(
                    did not in docs
                    or (not semantic_enabled and docs[did][0] not in q.get("evidence_sessions", []))
                    or docs[did][1].get("is_filler") for did in ids):
                issues.append({"code": "invalid_evidence_reference", "index": index})
            if q.get("qid") in actual_evidence and ids != actual_evidence[q.get("qid")]:
                issues.append({"code": "stale_candidate_evidence_pool", "index": index})
        counts = Counter(q.get("line") for q in questions)
        targets = report["delivery_contract"]
        floors = targets.get("per_line_min", {})
        missing = {line: floor - counts[line] for line, floor in floors.items() if counts[line] < floor}
        min_questions = targets.get("min_questions", 0)
        shortfall = max(0, min_questions - len(questions))
        if shortfall or missing:
            # Delivery targets describe planned yield.  Semantic review is allowed to
            # remove bad questions, so a smaller sound subset remains releasable.
            # Keep the shortfall visible for capacity planning without turning it
            # into a quality failure for the surviving questions.
            warnings.append({
                "code": "delivery_target_unmet",
                "effect": "planning_shortfall_only",
                "actual_questions": len(questions),
                "min_questions": min_questions,
                "shortfall": shortfall,
                "per_line_missing": missing,
            })
        active = [item.get("line") for item in wp.get("active_lines", []) if item.get("weight", 0) > 0]
        zero = [line for line in active if not counts[line]]
        if zero:
            warnings.append({"code": "planned_line_without_final_questions", "lines": zero})
        checks["coverage"] = {"final_count": len(questions), "by_line": dict(counts),
                              "planned_lines": active, "zero_output_lines": zero,
                              "required_floors": floors,
                              "delivery_target_met": not shortfall and not missing}
    except Exception as exc:
        issues.append({"code": "release_evaluation_error", "message": f"{type(exc).__name__}: {exc}"[:500]})
    report.update(status="failed" if issues else "passed", eligible=not issues)
    return report


def quality_snapshot(run_dir) -> dict:
    directory = Path(run_dir)
    base = {"version": VERSION, "status": "not_run", "eligible": False,
            "scope": [], "checks": {}, "issues": []}
    if _manual_rejection(directory):
        return {**base, "status": "failed", "issues": [{"code": "explicit_semantic_rejection"}]}
    path = directory / RELEASE_ARTIFACT
    if not path.is_file():
        return base
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(receipt, dict):
            raise ValueError("receipt must be an object")
        if (not isinstance(receipt.get("scope"), list)
                or not all(isinstance(s, str) for s in receipt["scope"])
                or not isinstance(receipt.get("checks"), dict)
                or not isinstance(receipt.get("issues"), list)
                or not all(isinstance(i, dict) and isinstance(i.get("code"), str) for i in receipt["issues"])
                or not isinstance(receipt.get("inputs"), dict)
                or type(receipt.get("eligible")) is not bool
                or receipt.get("status") not in ("passed", "failed")):
            raise ValueError("invalid release receipt schema")
        required_checks = {"seed_structure", "question_contracts", "world_answers", "corpus", "grounding", "coverage"}
        from pipeline.world_semantics import REVIEW_ARTIFACT as WORLD_REVIEW_ARTIFACT
        if WORLD_REVIEW_ARTIFACT in _release_inputs(directory):
            required_checks.add("world_semantics")
        if "03_orders_warning.json" in _release_inputs(directory):
            required_checks.add("order_warning_scope")
        from pipeline.seed_run import GENERATION_ARTIFACT
        if GENERATION_ARTIFACT in _release_inputs(directory):
            required_checks.add("seed_input_identity")
        if receipt["status"] == "passed" and (not receipt["scope"] or not required_checks.issubset(receipt["checks"])):
            raise ValueError("passed receipt lacks required checks")
        base.update({key: receipt.get(key, base[key]) for key in ("scope", "checks", "issues")})
        if (receipt.get("version") != VERSION or receipt.get("implementation_fingerprint") != implementation_fingerprint()
                or receipt.get("delivery_contract") != _delivery_contract(directory)
                or set(receipt.get("inputs", {})) != set(_release_inputs(directory))
                or any(not (directory / name).is_file() or file_hash(directory / name) != expected
                       for name, expected in receipt.get("inputs", {}).items())):
            return {**base, "status": "stale", "eligible": False}
        passed = receipt.get("status") == "passed" and receipt.get("eligible") is True and not receipt.get("issues")
        return {**base, "status": "passed" if passed else "failed", "eligible": passed}
    except (ValueError, OSError, TypeError, AttributeError) as exc:
        return {**base, "status": "failed", "issues": [{"code": "invalid_release_receipt", "message": str(exc)}]}


def require_release(bench_path, *, allow_unverified=False, corpus_path=None) -> dict:
    bench_path = Path(bench_path).resolve()
    snapshot = quality_snapshot(bench_path.parent)
    reasons = []
    if bench_path.name != "06_grounded_questions.json":
        reasons.append("benchmark is not the receipt-bound final artifact")
    if corpus_path is not None and Path(corpus_path).resolve() != bench_path.parent / "05_corpus.json":
        reasons.append("corpus differs from receipt-bound corpus")
    if reasons:
        snapshot = {**snapshot, "eligible": False, "status": "stale",
                    "issues": snapshot["issues"] + [{"code": "release_input_mismatch", "message": r} for r in reasons]}
    if snapshot["eligible"]:
        return snapshot
    if allow_unverified:
        return {**snapshot, "override": True, "evaluation_mode": "unverified_research"}
    raise ReleaseError(f"Benchmark not eligible for evaluation: {snapshot['status']}. "
                       "Run the quality stage, or explicitly use --allow-unverified for historical research.")
