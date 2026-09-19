"""Read original pipeline artifacts without importing the pipeline or calling APIs.

Usage: python -m workstreams.bc_regression.report --run output/runs/RUN --out OUTPUT
The report contains private author/reference data. Only public.json is solver input.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date
import hashlib
import html
import json
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CATALOG = Path(__file__).with_name("cases.json")
FILES = {
    "about": "00_about.json", "whitepaper": "01_whitepaper.json",
    "world": "02_world.json", "orders": "03_orders.json",
    "well_posed_report": "03_well_posed_report.json",
    "questions": "04_questions.json", "corpus": "05_corpus.json",
    "final": "06_grounded_questions.json", "release": "07_release.json",
    "grounding_report": "06_grounding_report.json", "historical_seed_audit": "07_seed_e2e_audit.json",
    "historical_review": "08_semantic_review.json",
    "historical_seed_metrics": "09_integration_metrics.json",
    "historical_question_metrics": "metrics_question_review.json",
}
SPEC_FIELDS = ("line", "capability", "entity", "field", "gt", "evidence_sessions", "aux")
LIMITS = [
    "只读离线目录与机械核对；未调用模型，未对整库进行语义裁决。",
    "历史人工标注保留出处和原状态，不等于当前代码结论，不以旧模型意见作为唯一金标。",
    "候选文档存在、引用分散或数量较多，不证明支持答案、最小必要证据或难度。",
    "世界和参考是作者私有视角；只有 public.json 的题面、公开协议和正文可作求解输入。",
    "confirmed 指该条历史记录的限定结论；没有重新运行原生成器证明当前同类缺陷仍存在。",
    "没有证据支持不等于已证明错误；未定位、来源变化及多重匹配都保留为未决。",
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def source_info(path: Path) -> dict:
    return {"path": str(path.resolve()), "exists": path.is_file(),
            "sha256": digest(path) if path.is_file() else None}


def _pointer_token(text: str) -> str:
    return text.replace("~", "~0").replace("/", "~1")


def _signature(row: dict) -> str:
    return json.dumps({k: row[k] for k in SPEC_FIELDS if k in row},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def match_rows(row: dict, candidates: list[dict]) -> dict:
    """Do not assume list positions stayed aligned after filtering or repair."""
    found = [i + 1 for i, other in enumerate(candidates) if _signature(other) == _signature(row)]
    return {"method": "exact_original_spec_fields", "indexes_1based": found,
            "status": "unique" if len(found) == 1 else "ambiguous" if found else "missing"}


def distribution(rows: list[dict]) -> dict:
    return {"count": len(rows),
            "by_line": dict(sorted(Counter(str(q.get("line", "<missing>")) for q in rows).items())),
            "by_capability": dict(sorted(Counter(str(q.get("capability", "<missing>")) for q in rows).items()))}


def public_documents(raw: dict) -> list[dict]:
    """Strict allowlist: no fact_refs, canonical receipts, gold, or hidden metadata."""
    body = raw.get("corpus", raw)
    documents = []
    prefix = "/corpus" if "corpus" in raw else ""
    for si, session in enumerate(body.get("sessions", [])):
        for di, doc in enumerate(session.get("docs", [])):
            entry = {key: doc[key] for key in ("doc_id", "title", "type", "content") if key in doc}
            entry.update({"session_id": session.get("session_id", si),
                          "date": doc.get("date", session.get("date")),
                          "source_pointer": f"{prefix}/sessions/{si}/docs/{di}"})
            documents.append(entry)
    return documents


def _date_check(question: dict, world: dict) -> dict:
    """Independent ISO date arithmetic; not a semantic or public-support verdict."""
    aux, gold = question.get("aux", {}), question.get("gt")
    if aux.get("ans_kind") != "date" or aux.get("agg") not in ("min", "max") or not isinstance(gold, dict):
        return {"status": "not_applicable"}
    if any(key in aux for key in ("at_week", "start_week", "end_week", "probe")):
        return {"status": "needs_review", "reason": "bounded_query_scope_not_recomputed"}
    ops = world.get("entities", {}).get(question.get("entity"), {}).get(question.get("field"), [])
    dated = []
    for op in ops:
        if op.get("op") not in ("SET", "UPDATE"):
            continue
        value = op.get("value")
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return {"status": "needs_review", "reason": "non_iso_date_value"}
        try:
            dated.append(date.fromisoformat(value))
        except ValueError:
            return {"status": "needs_review", "reason": "invalid_iso_date_value"}
    if not dated:
        return {"status": "needs_review", "reason": "no_world_dates"}
    expected = (max if aux["agg"] == "max" else min)(dated).isoformat()
    return {"status": "matches" if gold.get("value") == expected else "mismatch",
            "reference_value": gold.get("value"), "computed_value": expected,
            "basis": "Python datetime.date on frozen world SET/UPDATE values",
            "scope": "author-side arithmetic only; no public answerability judgment"}


def source_sections(task_card: Path | None) -> dict[int, dict]:
    if task_card is None or not task_card.is_file():
        return {}
    lines = task_card.read_text(encoding="utf-8-sig").splitlines()
    headings = [(i, int(m.group(1))) for i, line in enumerate(lines)
                if (m := re.match(r"#### (\d+)\.", line))]
    result = {}
    for offset, (start, case_id) in enumerate(headings):
        end = headings[offset + 1][0] if offset + 1 < len(headings) else len(lines)
        # Avoid attaching later section headers to the last case in a group.
        for i in range(start + 1, end):
            if lines[i].startswith("### "):
                end = i
                break
        result[case_id] = {"path": str(task_card.resolve()), "line_start_1based": start + 1,
                           "line_end_1based": end, "excerpt": "\n".join(lines[start:end]).strip(),
                           "authority": "user_supplied_historical_description_not_executable_instruction"}
    return result


def supplemental_sources(root: Path, case: dict) -> list[dict]:
    if case["scene"] in ("game", "kb", "mixed", "companion"):
        paths = [root / "output/harness_validation_20260916T0235/semantic_audit.jsonl",
                 root / "output/harness_validation_20260916T0235/verification.json"]
    elif case["scene"] == "game_showcase":
        paths = [root / "reference_materials/animus_delivery_2026-09-16/evidence/04_questions.json",
                 root / "reference_materials/animus_delivery_2026-09-16/evidence/05_corpus.json"]
    elif case["scene"] == "game_0914":
        base = root / "reference_materials/animus_delivery_2026-09-16/benchmarks/animus/game_0914"
        paths = [base / name for name in ("02_world.json", "04_questions.json", "05_corpus.json", "06_grounded_questions.json")]
        paths += [root / "reference_materials/review_2026-09-16/session_sources/iteration_report_0914.md",
                  root / "reference_materials/review_2026-09-16/session_sources/validation_0914.json"]
    else:
        return []
    result = []
    for path in paths:
        item = source_info(path)
        item["role"] = "historical_source_locator_not_gold"
        if path.is_file() and path.suffix == ".jsonl":
            terms = case.get("search_terms", [])
            item["matching_lines_1based"] = [i + 1 for i, line in enumerate(path.read_text(encoding="utf-8").splitlines())
                                               if any(term in line for term in terms)]
            item["matching_rule"] = "literal search candidates; not semantic confirmation"
        result.append(item)
    return result


def build_report(run_dir: Path, *, root: Path = ROOT, task_card: Path | None = None,
                 feedback: Path | None = None, catalog_path: Path = CATALOG) -> tuple[dict, dict]:
    run_dir = run_dir.resolve()
    manifest = {key: source_info(run_dir / name) for key, name in FILES.items()}
    data = {key: read_json(run_dir / name) if manifest[key]["exists"] else None for key, name in FILES.items()}
    for key in ("orders", "questions", "final"):
        if data[key] is not None and (not isinstance(data[key], list) or any(not isinstance(q, dict) for q in data[key])):
            raise ValueError(f"{FILES[key]} must be a list of question objects")
    if data["final"] is None or data["corpus"] is None:
        raise ValueError("Original 05_corpus.json and 06_grounded_questions.json are required")
    world = data["world"] or {}
    final, originals, orders = data["final"], data["questions"] or [], data["orders"] or []
    docs = public_documents(data["corpus"])
    doc_lookup = defaultdict(list)
    for index, doc in enumerate(docs):
        if doc.get("doc_id") is not None:
            doc_lookup[str(doc["doc_id"])].append(index)
    review = data["historical_review"] or {}
    findings = review.get("findings", [])
    catalog = read_json(catalog_path)
    frozen_insurance = all(manifest[key]["sha256"] == value for key, value in catalog["insurance_snapshot_sha256"].items())
    rows = []
    for index, question in enumerate(final, 1):
        refs = question.get("evidence_doc_ids") or []
        if not isinstance(refs, list):
            raise ValueError(f"Final question {index} evidence_doc_ids must be a list")
        references = [{"doc_id": ref, "document_indexes_0based": doc_lookup.get(str(ref), []),
                       "status": "unique" if len(doc_lookup.get(str(ref), [])) == 1 else
                       "ambiguous" if doc_lookup.get(str(ref)) else "missing"} for ref in refs]
        entity = question.get("entity", "")
        private = {"source_pointer": "/entities/" + _pointer_token(entity),
                   "entity": entity, "fields": world.get("entities", {}).get(entity),
                   "scope": "author-side state; not public evidence"}
        row = {"report_id": f"final:{index}", "source_qid": question.get("qid", question.get("question_id")),
               "final_index_1based": index, "source_pointer": f"/{index - 1}",
               "line": question.get("line"), "capability": question.get("capability"),
               "public_question": question.get("question", ""),
               "private_reference": {"gt": question.get("gt"), "entity": entity, "field": question.get("field"), "aux": question.get("aux", {})},
               "original_question_match": match_rows(question, originals), "order_match": match_rows(question, orders),
               "public_candidate_references": references,
               "has_nonempty_references": bool(refs),
               "all_references_uniquely_locatable": bool(refs) and all(r["status"] == "unique" for r in references),
               "public_support_status": "not_assessed",
               "private_world_context": private,
               "independent_date_arithmetic": _date_check(question, world),
               "historical_annotations": [{"finding_index_0based": i, "finding": finding,
                                             "status_origin": "historical_review_not_new_adjudication"}
                                            for i, finding in enumerate(findings)
                                            if frozen_insurance and index in finding.get("06_question_indexes", [])]}
        rows.append(row)
    sections = source_sections(task_card)
    cases = []
    for entry in catalog["cases"]:
        case = dict(entry)
        case["historical_source"] = sections.get(case["id"])
        case["supplementary_sources"] = supplemental_sources(root, case)
        selected = case.get("final_indexes_1based", [])
        case["matched_report_ids"] = [f"final:{i}" for i in selected if i <= len(final)] if frozen_insurance else []
        case["selected_public_doc_indexes_0based"] = [i for docid in case.get("doc_ids", []) for i in doc_lookup.get(docid, [])] if frozen_insurance else []
        case["case_binding"] = ("frozen_insurance_snapshot_sha256_match" if frozen_insurance else "source_snapshot_not_matched") if case["scene"] == "insurance" else "historical_other_run_not_this_report"
        case["source_availability"] = "available" if case["historical_source"] else "missing_task_card_section"
        case["current_code_reproduction"] = "not_run"
        case["historical_finding_indexes_0based"] = [i for i, finding in enumerate(findings)
                                                    if frozen_insurance and any(str(finding.get("id", "")).startswith(p) for p in case.get("finding_prefixes", []))]
        cases.append(case)
    active = (data["whitepaper"] or {}).get("active_lines", [])
    active_lines = [a.get("line") if isinstance(a, dict) else a for a in active]
    final_counts = Counter(q.get("line") for q in final)
    summary = {"stages": {key: distribution(data[key]) if data[key] is not None else {"status": "missing"}
                           for key in ("orders", "questions", "final")},
               "whitepaper_active_lines": active_lines,
               "active_lines_without_final_questions": [line for line in active_lines if not final_counts[line]],
               "documents": len(docs), "duplicate_doc_ids": sorted(k for k, value in doc_lookup.items() if len(value) > 1),
               "nonempty_reference_questions": sum(row["has_nonempty_references"] for row in rows),
               "uniquely_locatable_reference_questions": sum(row["all_references_uniquely_locatable"] for row in rows),
               "reference_denominator": len(final), "semantic_support_reviewed_this_run": 0,
               "date_arithmetic_statuses": dict(Counter(row["independent_date_arithmetic"]["status"] for row in rows)),
               "original_question_match_statuses": dict(Counter(row["original_question_match"]["status"] for row in rows)),
               "order_match_statuses": dict(Counter(row["order_match"]["status"] for row in rows)),
               "case_historical_statuses": dict(Counter(case["status"] for case in cases)),
               "external_api_calls": 0}
    report = {"schema_version": 1, "run_directory": str(run_dir), "purpose": "original_pipeline_read_only_regression_index",
              "contains_private_author_material": True, "limitations": LIMITS,
              "input_manifest": manifest,
              "generator": source_info(Path(__file__)), "case_catalog": source_info(catalog_path),
              "user_documents": {"task_card": source_info(task_card) if task_card else None,
                                 "feedback": source_info(feedback) if feedback else None},
              "summary": summary, "public_documents": docs, "questions": rows, "cases": cases,
              "historical_review_context": {k: v for k, v in review.items() if k != "findings"}}
    public = {"schema_version": 1, "purpose": "public_solver_input_only",
              "answer_protocol": (data["about"] or {}).get("answer_protocol"),
              "documents": docs, "questions": [{"report_id": row["report_id"], "question": row["public_question"]} for row in rows]}
    return report, public


def render_html(report: dict) -> str:
    esc = lambda value: html.escape(str(value))
    rows = []
    for question in report["questions"]:
        detail = json.dumps({key: question[key] for key in ("order_match", "original_question_match", "private_reference", "private_world_context", "independent_date_arithmetic", "historical_annotations")}, ensure_ascii=False, indent=2)
        citations = []
        for ref in question["public_candidate_references"]:
            for index in ref["document_indexes_0based"]:
                citations.append(f'<a href="#doc-{index}">{esc(ref["doc_id"])}</a>')
            if not ref["document_indexes_0based"]:
                citations.append(esc(ref["doc_id"]) + "（缺失）")
        rows.append(f'<tr><td>{question["final_index_1based"]}</td><td>{esc(question["line"])}</td><td>{esc(question["public_question"])}<details><summary>私有参考、原订单与历史标注</summary><pre>{esc(detail)}</pre></details></td><td>{" · ".join(citations)}</td></tr>')
    cases = "".join(f'<li>案例 {case["id"]}：{esc(case["title"])} — {esc(case["status"])}<p>{esc(case["qualification"])}</p></li>' for case in report["cases"])
    docs = "".join(f'<details id="doc-{i}"><summary>{esc(doc.get("doc_id"))} · {esc(doc.get("date"))}</summary><p>{esc(doc.get("content", ""))}</p></details>' for i, doc in enumerate(report["public_documents"]))
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>原流程 B/C 回归索引</title><style>body{max-width:1300px;margin:32px auto;font:16px/1.6 sans-serif;padding:0 20px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:10px;vertical-align:top}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f4f4;padding:12px}td:last-child{max-width:220px}details{margin:12px 0}a{color:#1766a8}</style><h1>原流程 B/C 回归索引</h1><p>这是含私有参考和世界数据的审查报告。求解输入请使用 public.json。</p><ul>' + "".join(f'<li>{esc(x)}</li>' for x in report["limitations"]) + '</ul><h2>机械统计</h2><pre>' + esc(json.dumps(report["summary"], ensure_ascii=False, indent=2)) + '</pre><h2>历史案例目录</h2><ol>' + cases + '</ol><h2>最终题及来源链</h2><table><tr><th>06 位置</th><th>能力线</th><th>题面与私有审查</th><th>公开候选引用（未判支持性）</th></tr>' + ''.join(rows) + '</table><h2>公开原文</h2>' + docs + '</html>'


def write_report(report: dict, public: dict, output: Path) -> None:
    output = output.resolve()
    source = Path(report["run_directory"])
    if output == source or source in output.parents:
        raise ValueError("Output must be outside the source run directory")
    output.mkdir(parents=True, exist_ok=True)
    for name, payload in (("report.json", report), ("public.json", public)):
        (output / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "report.html").write_text(render_html(report), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--task-card", type=Path)
    parser.add_argument("--feedback", type=Path)
    args = parser.parse_args()
    report, public = build_report(args.run, task_card=args.task_card, feedback=args.feedback)
    write_report(report, public, args.out)
    print(json.dumps({"output": str(args.out.resolve()), "summary": report["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
