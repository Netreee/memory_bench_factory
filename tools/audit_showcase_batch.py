#!/usr/bin/env python3
"""统一验收叙事型游戏 Benchmark Run，并保存可复盘的机器报告。

输入一个或多个 Run 目录。脚本只读取正式产物，并把每个 Run 的检查结果
写入 ``production/FINAL_ACCEPTANCE.json``；多个 Run 时还会打印横向相似度。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.grounding import run_grounding
from pipeline.well_posed import run_well_posed
from pipeline.world_state import WorldState
from tools.audit_run import audit_run as repository_audit_run
from tools.showcase_integrity import (
    CANONICAL_ACCEPTANCE_SUMMARY,
    CANONICAL_BATCH_ID,
    CANONICAL_RELEASE_PROFILE,
    CANONICAL_RELEASE_PROFILE_SHA256,
    CANONICAL_REVIEW_REQUIRED_RUN_IDS,
    CANONICAL_RUN_IDS,
    release_content_digest,
    review_target_digest,
    sha256_file,
)


REQUIRED_FILES = [
    "00_input.json",
    "00_about.json",
    "01_whitepaper.json",
    "02_world.json",
    "03_orders.json",
    "03_well_posed_report.json",
    "04_questions.json",
    "05_corpus.json",
    "06_grounded_questions.json",
    "06_grounding_report.json",
    "story_bible.json",
    "scene_ledger.json",
    "quality_report.json",
    "promo_display_pack.json",
    "strict_scoring_contract.json",
    "manifest.json",
    "README.md",
    "run.log",
    "production/SELF_WORKLOG.md",
    "production/CREATIVE_RATIONALE.md",
]

EXPECTED_LINES = {
    "L1_timeline": 3,
    "L2_relational": 5,
    "L3_process": 3,
    "L5_conflict": 3,
    "L6_refusal": 2,
    "L7_consolidation": 2,
}

FINAL_REVIEW_FILES = [
    "production/CROSS_REVIEW.md",
    "production/REVISION_LOG.md",
    "production/FINAL_REVIEW.md",
    "production/review_chain.json",
]

RELEASE_ASSET_FILES = [
    "STORY_BIBLE.md",
    "SHOWCASE_CUT.md",
    "PROMO_DISPLAY_PACK.md",
]

REVIEW_SCORE_KEYS = {
    "hook",
    "continuity",
    "agency",
    "mechanism",
    "irreversible_cost",
    "evidence_drama",
    "star_questions",
    "game_visuals",
}

DISTINCTIVENESS_KEYS = {
    "protagonist_role",
    "spatial_form",
    "core_mechanism",
    "primary_gameplay",
    "evidence_media",
    "final_cost_type",
    "key_visual",
}


def read_json(path: Path) -> Any:
    """读取 UTF-8 JSON。"""
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: Any) -> None:
    """先写同目录临时文件，再原子替换 JSON 目标。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def corpus_docs(corpus_obj: dict) -> tuple[list[dict], dict[str, int]]:
    """摊平 corpus 文档，并返回文档所属 session。"""
    sessions = corpus_obj.get("corpus", {}).get("sessions", [])
    docs: list[dict] = []
    doc_sessions: dict[str, int] = {}
    for session in sessions:
        session_id = session.get("session_id")
        for doc in session.get("docs", []):
            docs.append(doc)
            doc_sessions[doc.get("doc_id", "")] = session_id
    return docs, doc_sessions


def token_count(text: str) -> int | None:
    """按 o200k_base 统计 token；环境没有 tiktoken 时返回空值。"""
    try:
        import tiktoken
    except ImportError:
        return None
    return len(tiktoken.get_encoding("o200k_base").encode(text))


def valid_fact_refs(world: dict) -> set[str]:
    """收集世界中可被 corpus 引用的事实锚点。"""
    refs = {
        f"{entity}.{field}"
        for entity, fields in world.get("entities", {}).items()
        for field in fields
    }
    refs.update(item.get("id") for item in world.get("events", []) if item.get("id"))
    refs.update(item.get("id") for item in world.get("relations", []) if item.get("id"))
    refs.update(item.get("rule_id") for item in world.get("cascades", []) if item.get("rule_id"))
    return refs


def protagonist_name(run_dir: Path, story_bible: dict) -> str:
    """从故事圣经或白皮书取得唯一主角名。"""
    protagonist = story_bible.get("protagonist")
    if isinstance(protagonist, dict) and protagonist.get("name"):
        return str(protagonist["name"])
    whitepaper = read_json(run_dir / "01_whitepaper.json")
    return str(whitepaper.get("story_contract", {}).get("protagonist", ""))


def validate_review_chain(
    run_dir: Path,
    expected_roles: dict[str, str] | None = None,
) -> tuple[dict, list[str]]:
    """验证非作者复审身份、八项评分、报告哈希与被审内容摘要。"""
    failures: list[str] = []
    chain_path = run_dir / "production/review_chain.json"
    if not chain_path.is_file():
        return {}, ["缺少文件:production/review_chain.json"]
    try:
        chain = read_json(chain_path)
    except Exception as exc:  # noqa: BLE001
        return {}, [f"review_chain.json 无法解析:{exc!r}"]

    if chain.get("run_id") != run_dir.name:
        failures.append(f"review chain run_id 不符:{chain.get('run_id')}")
    for role in ("author", "cross_reviewer", "final_reviewer"):
        value = chain.get(role)
        if not isinstance(value, str) or not value.strip():
            failures.append(f"review chain 缺少角色:{role}")
        if expected_roles and value != expected_roles.get(role):
            failures.append(
                f"review chain 角色不符:{role}={value!r},expected={expected_roles.get(role)!r}"
            )
    if chain.get("author") in {chain.get("cross_reviewer"), chain.get("final_reviewer")}:
        failures.append("作者与交叉/最终审查者未隔离")
    if chain.get("cross_reviewer") != chain.get("final_reviewer"):
        failures.append("最终审查者不是原交叉审查者")
    if chain.get("cross_review_verdict") not in {"REVISE", "REJECT"}:
        failures.append(f"初审 verdict 非有效阻断结论:{chain.get('cross_review_verdict')}")
    if chain.get("final_verdict") != "PASS":
        failures.append(f"最终审查 verdict 不是 PASS:{chain.get('final_verdict')}")

    scores = chain.get("scores")
    if not isinstance(scores, dict) or set(scores) != REVIEW_SCORE_KEYS:
        failures.append(
            f"终审八项评分键不完整:{sorted(scores) if isinstance(scores, dict) else scores!r}"
        )
        valid_scores: dict[str, int] = {}
    else:
        valid_scores = {
            key: value
            for key, value in scores.items()
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 5
        }
        if len(valid_scores) != len(REVIEW_SCORE_KEYS):
            failures.append("终审评分必须为 0–5 的整数")
        low_scores = {key: value for key, value in valid_scores.items() if value < 3}
        if low_scores:
            failures.append(f"终审存在低于 3 分的维度:{low_scores}")
    calculated_total = sum(valid_scores.values())
    if chain.get("total_score") != calculated_total:
        failures.append(
            f"终审总分与八项求和不符:{chain.get('total_score')}!={calculated_total}"
        )
    if calculated_total < 30:
        failures.append(f"终审总分低于 30:{calculated_total}/40")
    if chain.get("vetoes") != []:
        failures.append(f"终审仍有一票否决项:{chain.get('vetoes')!r}")

    report_paths = {
        "cross_review_sha256": run_dir / "production/CROSS_REVIEW.md",
        "revision_log_sha256": run_dir / "production/REVISION_LOG.md",
        "final_review_sha256": run_dir / "production/FINAL_REVIEW.md",
    }
    for field, path in report_paths.items():
        if not path.is_file():
            failures.append(f"审查报告缺失:{path.relative_to(run_dir)}")
        elif chain.get(field) != sha256_file(path):
            failures.append(f"审查报告哈希不符:{field}")

    computed_review_digest = review_target_digest(run_dir)
    if chain.get("reviewed_content_sha256") != computed_review_digest:
        failures.append(
            "终审绑定的内容摘要与当前作者修订内容不符:"
            f"{chain.get('reviewed_content_sha256')}!={computed_review_digest}"
        )

    final_review_path = run_dir / "production/FINAL_REVIEW.md"
    final_review = final_review_path.read_text(encoding="utf-8") if final_review_path.is_file() else ""
    verdict_match = re.search(
        r"(?m)^FINAL_VERDICT:\s*(PASS|REVISE|REJECT)\s*$",
        final_review,
    )
    score_match = re.search(r"(?m)^TOTAL_SCORE:\s*(\d+)/40\s*$", final_review)
    digest_match = re.search(
        r"(?m)^REVIEWED_CONTENT_SHA256:\s*([0-9a-f]{64})\s*$",
        final_review,
    )
    if not verdict_match or verdict_match.group(1) != chain.get("final_verdict"):
        failures.append("FINAL_REVIEW 与 review_chain 的 verdict 不一致")
    if not score_match or int(score_match.group(1)) != calculated_total:
        failures.append("FINAL_REVIEW 与 review_chain 的总分不一致")
    if not digest_match or digest_match.group(1) != computed_review_digest:
        failures.append("FINAL_REVIEW 未绑定当前 reviewed content digest")

    return {
        "status": "PASS" if not failures else "FAIL",
        "review_target_sha256": computed_review_digest,
        "author": chain.get("author"),
        "cross_reviewer": chain.get("cross_reviewer"),
        "final_reviewer": chain.get("final_reviewer"),
        "total_score": calculated_total,
        "scores": valid_scores,
    }, failures


def audit_run(
    run_dir: Path,
    require_review_chain: bool = False,
    expected_roles: dict[str, str] | None = None,
    release_mode: bool = False,
) -> dict:
    """对单个 Run 执行结构、接地、引用和宣传资产检查。"""
    failures: list[str] = []
    warnings: list[str] = []

    required_files = (
        REQUIRED_FILES
        + (FINAL_REVIEW_FILES if require_review_chain else [])
        + (RELEASE_ASSET_FILES if release_mode else [])
    )
    missing_files = [name for name in required_files if not (run_dir / name).exists()]
    if missing_files:
        return {
            "run_id": run_dir.name,
            "status": "FAIL",
            "failures": [f"缺少文件:{name}" for name in missing_files],
            "warnings": [],
            "metrics": {},
        }

    review_chain_report = None
    if require_review_chain:
        review_chain_report, review_failures = validate_review_chain(
            run_dir,
            expected_roles=expected_roles,
        )
        failures.extend(review_failures)

    # 先运行仓库原生出厂审计，避免本工具的展示层加严规则掩盖蓝图兼容性问题。
    try:
        repository_result = repository_audit_run(run_dir)
        failures.extend(f"仓库原生审计:{message}" for message in repository_result.failures)
        warnings.extend(f"仓库原生审计:{message}" for message in repository_result.warnings)
        repository_audit = {
            "status": "PASS" if not repository_result.failures else "FAIL",
            "passes": repository_result.passes,
            "warnings": repository_result.warnings,
            "failures": repository_result.failures,
        }
    except Exception as exc:  # noqa: BLE001
        repository_audit = {"status": "FAIL", "exception": repr(exc)}
        failures.append(f"仓库原生审计异常:{exc!r}")

    corpus_obj = read_json(run_dir / "05_corpus.json")
    world = read_json(run_dir / "02_world.json")
    questions = read_json(run_dir / "06_grounded_questions.json")
    drafted_questions = read_json(run_dir / "04_questions.json")
    orders = read_json(run_dir / "03_orders.json")
    story_bible = read_json(run_dir / "story_bible.json")
    promo = read_json(run_dir / "promo_display_pack.json")
    strict_contract = read_json(run_dir / "strict_scoring_contract.json")

    docs, doc_sessions = corpus_docs(corpus_obj)
    sessions = corpus_obj.get("corpus", {}).get("sessions", [])
    by_id = {doc.get("doc_id"): doc for doc in docs}
    signal_docs = [doc for doc in docs if not doc.get("is_filler")]
    filler_docs = [doc for doc in docs if doc.get("is_filler")]

    if len(sessions) != 6:
        failures.append(f"session 数不是 6:{len(sessions)}")
    bad_session_sizes = [
        {"session_id": session.get("session_id"), "docs": len(session.get("docs", []))}
        for session in sessions
        if len(session.get("docs", [])) != 5
    ]
    if bad_session_sizes:
        failures.append(f"存在非 5 文档章节:{bad_session_sizes}")
    if len(docs) != 30 or len(signal_docs) != 24 or len(filler_docs) != 6:
        failures.append(
            f"文档配比错误:all={len(docs)},signal={len(signal_docs)},filler={len(filler_docs)}"
        )
    if len(by_id) != len(docs):
        failures.append("doc_id 不唯一")
    if any(doc.get("fact_refs") or doc.get("claim_refs") for doc in filler_docs):
        failures.append("filler 携带主线事实引用")

    raw_chars = sum(len(str(doc.get("content", ""))) for doc in docs)
    joined_text = "\n".join(str(doc.get("content", "")) for doc in docs)
    tokens = token_count(joined_text)
    if raw_chars < 6000:
        failures.append(f"Corpus 正文少于 6000 字符:{raw_chars}")
    if release_mode and tokens is None:
        failures.append("发布验收无法取得 o200k token 数；请在完整项目环境运行")

    protagonist = protagonist_name(run_dir, story_bible)
    protagonist_aliases = {
        alias
        for alias in {protagonist, protagonist.split("·", 1)[0], protagonist.split("・", 1)[0]}
        if alias
    }
    protagonist_signal_docs = [
        doc
        for doc in signal_docs
        if any(alias in doc.get("content", "") for alias in protagonist_aliases)
    ]
    protagonist_coverage = len(protagonist_signal_docs) / max(1, len(signal_docs))
    if protagonist_coverage < 0.80:
        failures.append(f"主角 signal 覆盖低于 80%:{protagonist_coverage:.1%}")

    if len(questions) != 18:
        failures.append(f"问题数不是 18:{len(questions)}")
    if len({item.get("qid") for item in questions}) != len(questions):
        failures.append("qid 不唯一")
    line_counts = Counter(item.get("line") for item in questions)
    if dict(line_counts) != EXPECTED_LINES:
        failures.append(f"生产线分布错误:{dict(line_counts)}")
    stars = [item for item in questions if item.get("star")]
    if len(stars) != 6:
        failures.append(f"明星问题不是 6 道:{len(stars)}")
    if questions[-6:] != stars:
        failures.append("明星问题没有集中在数组末尾")

    if release_mode:
        order_by_qid = {item.get("qid"): item for item in orders}
        draft_by_qid = {item.get("qid"): item for item in drafted_questions}
        grounded_by_qid = {item.get("qid"): item for item in questions}
        if set(order_by_qid) != set(draft_by_qid) or set(draft_by_qid) != set(grounded_by_qid):
            failures.append("03/04/06 的 qid 集合不一致")
        for qid in sorted(set(order_by_qid) & set(draft_by_qid) & set(grounded_by_qid)):
            order = order_by_qid[qid]
            draft = draft_by_qid[qid]
            grounded_question = grounded_by_qid[qid]
            order_drift = [
                key
                for key, value in order.items()
                if draft.get(key) != value or grounded_question.get(key) != value
            ]
            draft_drift = [
                key for key, value in draft.items() if grounded_question.get(key) != value
            ]
            if order_drift or draft_drift:
                failures.append(
                    f"{qid} 在 03/04/06 间漂移:order={order_drift},draft={draft_drift}"
                )
        empty_evidence = [item.get("qid") for item in questions if not item.get("evidence_doc_ids")]
        empty_atoms = [item.get("qid") for item in questions if not item.get("answer_atoms")]
        if empty_evidence:
            failures.append(f"发布题目显式 evidence_doc_ids 为空:{empty_evidence}")
        if empty_atoms:
            failures.append(f"发布题目 answer_atoms 为空:{empty_atoms}")

    question_checks = []
    for question in questions:
        evidence_ids = question.get("evidence_doc_ids", [])
        missing_docs = [doc_id for doc_id in evidence_ids if doc_id not in by_id]
        out_of_scope = [
            doc_id
            for doc_id in evidence_ids
            if doc_id in doc_sessions and doc_sessions[doc_id] not in question.get("evidence_sessions", [])
        ]
        evidence_text = "\n".join(by_id[doc_id].get("content", "") for doc_id in evidence_ids if doc_id in by_id)
        missing_atoms = [atom for atom in question.get("answer_atoms", []) if str(atom) not in evidence_text]
        ok = not missing_docs and not out_of_scope and not missing_atoms
        if not ok:
            failures.append(
                f"{question.get('qid')} 证据闭包失败:missing_docs={missing_docs},"
                f"out_of_scope={out_of_scope},missing_atoms={missing_atoms}"
            )
        question_checks.append(
            {
                "qid": question.get("qid"),
                "ok": ok,
                "missing_docs": missing_docs,
                "out_of_scope": out_of_scope,
                "missing_atoms": missing_atoms,
            }
        )

    allowed_refs = valid_fact_refs(world)
    dangling_refs = sorted(
        {
            ref
            for doc in signal_docs
            for ref in (doc.get("fact_refs") or [])
            if ref not in allowed_refs
        }
    )
    if dangling_refs:
        failures.append(f"fact_refs 悬空:{dangling_refs}")

    conflict_docs = [doc for doc in signal_docs if doc.get("is_conflict")]
    if len(conflict_docs) < 3:
        failures.append(f"刻意冲突文档少于 3:{len(conflict_docs)}")
    refusal_questions = [item for item in questions if item.get("line") == "L6_refusal"]
    if any(str(item.get("gt")) not in {"INSUFFICIENT", "INSUFFICIENT_EVIDENCE"} for item in refusal_questions):
        failures.append("L6 拒答题没有使用信息不足 sentinel")

    try:
        world_state = WorldState.from_dict(world)
        well_posed, wp_report = run_well_posed(orders, world_state)
        if len(well_posed) != 18:
            failures.append(f"正式 well-posed 不是 18/18:{len(well_posed)}/18")
    except Exception as exc:  # noqa: BLE001
        wp_report = {"exception": repr(exc)}
        failures.append(f"正式 well-posed 异常:{exc!r}")

    try:
        grounded, grounding_report = run_grounding(questions, corpus_obj)
        if len(grounded) != 18:
            failures.append(f"正式 grounding 不是 18/18:{len(grounded)}/18")
    except Exception as exc:  # noqa: BLE001
        grounding_report = {"exception": repr(exc)}
        failures.append(f"正式 grounding 异常:{exc!r}")

    promo_chapters = promo.get("chapters", [])
    promo_stars = promo.get("star_questions", [])
    if len(promo_chapters) != 6 or len(promo_stars) != 6:
        failures.append(
            f"宣传展示包不完整:chapters={len(promo_chapters)},stars={len(promo_stars)}"
        )
    if release_mode:
        if [item.get("qid") for item in promo_stars] != [item.get("qid") for item in stars]:
            failures.append("宣传展示包的六道明星题与正式题库顺序不一致")
        official_stars = {item.get("qid"): item for item in stars}
        for promo_item in promo_stars:
            qid = promo_item.get("qid")
            official = official_stars.get(qid)
            if not official:
                continue
            if promo_item.get("question") != official.get("question"):
                failures.append(f"{qid} 宣传题面与正式题面漂移")
            reveal = promo_item.get("answer_reveal", promo_item.get("gold"))
            if reveal != official.get("gt"):
                failures.append(f"{qid} 宣传答案与正式 gold 漂移")
            if "strict_scoring" in promo_item and promo_item.get("strict_scoring") != official.get("strict_scoring"):
                failures.append(f"{qid} 宣传严格评分合同与正式题漂移")
            if "answer_atoms" in promo_item and promo_item.get("answer_atoms") != official.get("answer_atoms"):
                failures.append(f"{qid} 宣传 answer_atoms 与正式题漂移")
            if "evidence_doc_ids" in promo_item and promo_item.get("evidence_doc_ids") != official.get("evidence_doc_ids"):
                failures.append(f"{qid} 宣传证据与正式题漂移")
        for chapter in promo_chapters:
            source_doc_id = chapter.get("source_doc_id", chapter.get("hero_doc_id"))
            if source_doc_id and source_doc_id not in by_id:
                failures.append(f"宣传章节引用不存在的文档:{source_doc_id}")
    strict_items = strict_contract.get("items", [])
    strict_qid_list = [item.get("qid") for item in strict_items]
    strict_qids = set(strict_qid_list)
    embedded_strict_qids = {
        item.get("qid") for item in questions if item.get("strict_scoring")
    }
    if len(strict_qid_list) != len(strict_qids):
        failures.append("严格评分合同存在重复 qid")
    missing_contract_entries = sorted(embedded_strict_qids - strict_qids)
    orphan_contract_entries = sorted(strict_qids - embedded_strict_qids)
    if missing_contract_entries:
        failures.append(f"题目内严格评分未进入独立合同:{missing_contract_entries}")
    if orphan_contract_entries:
        failures.append(f"独立严格评分合同存在孤儿项:{orphan_contract_entries}")
    complex_star_qids = {
        item.get("qid")
        for item in stars
        if isinstance(item.get("gt"), (list, dict)) or len(item.get("answer_atoms", [])) > 1
    }
    missing_strict = sorted(complex_star_qids - strict_qids)
    if missing_strict:
        failures.append(f"复合明星题缺少严格评分契约:{missing_strict}")

    # 用仓库真实判分入口验证合同，而不是只检查 strict_scoring 字段存在。
    # 每份合同都要满足：完整原子答案通过；删去任一必需原子后均不通过。
    try:
        from eval.judge import judge_answer as strict_judge_answer
    except Exception as exc:  # noqa: BLE001
        strict_judge_answer = None
        failures.append(f"严格评分器导入失败:{exc!r}")

    question_by_qid = {item.get("qid"): item for item in questions}
    strict_checks = []
    for contract_item in strict_items:
        qid = contract_item.get("qid")
        policy = contract_item.get("policy")
        required_atoms = [
            str(atom)
            for atom in (contract_item.get("required_atoms") or [])
            if str(atom).strip()
        ]
        question = question_by_qid.get(qid)
        embedded = (question or {}).get("strict_scoring") or {}
        contract_matches_question = (
            embedded.get("policy") == policy
            and [str(atom) for atom in (embedded.get("required_atoms") or [])] == required_atoms
        )
        contract_gold_matches = (
            "gold" not in contract_item
            or (question is not None and contract_item.get("gold") == question.get("gt"))
        )
        question_gt = (question or {}).get("gt")
        composite_gold = (
            isinstance(question_gt, (list, dict))
            or (isinstance(question_gt, str) and ("；" in question_gt or ";" in question_gt))
        )
        # answer_atoms 同时承担证据接地，可能包含题面冲突值，不能拿它推断答案槽数。
        atom_count_sufficient = bool(required_atoms) and (
            not composite_gold or len(required_atoms) >= 2
        )
        full_pass = False
        canonical_gold_pass = False
        leave_one_out = []
        negation_checks = []
        if (
            strict_judge_answer is not None
            and question
            and policy == "all_required_atoms"
            and required_atoms
        ):
            probe = dict(question)
            probe["strict_scoring"] = {
                "policy": policy,
                "required_atoms": required_atoms,
            }
            full_answer = "；".join(required_atoms)
            full_pass = strict_judge_answer(probe, full_answer, use_llm=False)
            canonical_gold = (
                question.get("gt")
                if isinstance(question.get("gt"), str)
                else json.dumps(question.get("gt"), ensure_ascii=False)
            )
            canonical_gold_pass = strict_judge_answer(
                probe,
                str(canonical_gold),
                use_llm=False,
            )
            for omitted_index, omitted_atom in enumerate(required_atoms):
                partial_answer = "；".join(
                    atom for index, atom in enumerate(required_atoms) if index != omitted_index
                )
                leave_one_out.append(
                    {
                        "omitted_atom": omitted_atom,
                        "incorrectly_passed": strict_judge_answer(
                            probe, partial_answer, use_llm=False
                        ),
                    }
                )
                negated_answer = "；".join(
                    [
                        atom
                        for index, atom in enumerate(required_atoms)
                        if index != omitted_index
                    ]
                    + [f"不是{omitted_atom}"]
                )
                negation_checks.append(
                    {
                        "negated_atom": omitted_atom,
                        "incorrectly_passed": strict_judge_answer(
                            probe,
                            negated_answer,
                            use_llm=False,
                        ),
                    }
                )

        strict_ok = (
            question is not None
            and policy == "all_required_atoms"
            and bool(required_atoms)
            and contract_matches_question
            and contract_gold_matches
            and atom_count_sufficient
            and full_pass
            and canonical_gold_pass
            and not any(item["incorrectly_passed"] for item in leave_one_out)
            and not any(item["incorrectly_passed"] for item in negation_checks)
        )
        if not strict_ok:
            failures.append(
                f"{qid} 严格评分合同失败:question={question is not None},"
                f"policy={policy},atoms={len(required_atoms)},"
                f"contract_match={contract_matches_question},gold_match={contract_gold_matches},"
                f"atom_count={atom_count_sufficient},full_pass={full_pass},"
                f"canonical_gold_pass={canonical_gold_pass},"
                f"leave_one_out_passes={sum(item['incorrectly_passed'] for item in leave_one_out)},"
                f"negation_passes={sum(item['incorrectly_passed'] for item in negation_checks)}"
            )
        strict_checks.append(
            {
                "qid": qid,
                "ok": strict_ok,
                "required_atoms": required_atoms,
                "contract_matches_question": contract_matches_question,
                "contract_gold_matches_question": contract_gold_matches,
                "atom_count_sufficient": atom_count_sufficient,
                "full_answer_passed": full_pass,
                "canonical_gold_passed": canonical_gold_pass,
                "leave_one_out": leave_one_out,
                "negation_checks": negation_checks,
            }
        )

    if not story_bible.get("protagonist", {}).get("final_cost"):
        failures.append("故事圣经未记录不可逆代价")
    scenes = read_json(run_dir / "scene_ledger.json")
    if len(scenes) != 6:
        failures.append(f"scene ledger 不是 6 章:{len(scenes)}")
    visual_count = sum(bool(scene.get("visual") or scene.get("set_piece") or scene.get("hook")) for scene in scenes)
    if visual_count < 3:
        warnings.append(f"可机器识别的视觉场景少于 3:{visual_count}")

    try:
        content_digest = release_content_digest(run_dir)
    except Exception as exc:  # noqa: BLE001
        content_digest = None
        failures.append(f"发布内容树摘要失败:{exc!r}")

    result = {
        "run_id": run_dir.name,
        "status": "PASS" if not failures and not warnings else "FAIL",
        "failures": failures,
        "warnings": warnings,
        "metrics": {
            "sessions": len(sessions),
            "documents": len(docs),
            "signal_documents": len(signal_docs),
            "filler_documents": len(filler_docs),
            "corpus_chars": raw_chars,
            "corpus_tokens_o200k": tokens,
            "entities": len(world.get("entities", {})),
            "entity_types": len(set(world.get("entity_types", {}).values())),
            "relations": len(world.get("relations", [])),
            "events": len(world.get("events", [])),
            "conflict_documents": len(conflict_docs),
            "questions": len(questions),
            "line_counts": dict(line_counts),
            "star_questions": len(stars),
            "strict_scoring_contracts": len(strict_checks),
            "strict_scoring_passed": sum(item["ok"] for item in strict_checks),
            "protagonist": protagonist,
            "protagonist_signal_coverage": round(protagonist_coverage, 4),
        },
        "question_checks": question_checks,
        "strict_scoring_checks": strict_checks,
        "review_chain": review_chain_report,
        "content_tree_sha256": content_digest,
        "repository_audit": repository_audit,
        "formal_well_posed": wp_report,
        "formal_grounding": grounding_report,
    }
    return result


def normalized_ngrams(text: str, n: int = 8) -> set[str]:
    """生成用于横向去同质化的中文字符 n-gram。"""
    normalized = re.sub(r"\s+", "", text)
    return {normalized[index:index + n] for index in range(max(0, len(normalized) - n + 1))}


def distinctiveness(results: list[tuple[Path, dict]]) -> list[dict]:
    """计算 Run 两两正文 n-gram Jaccard，相似度过高时报警。"""
    texts: dict[str, str] = {}
    for run_dir, _ in results:
        if not (run_dir / "05_corpus.json").exists():
            continue
        docs, _ = corpus_docs(read_json(run_dir / "05_corpus.json"))
        texts[run_dir.name] = "\n".join(doc.get("content", "") for doc in docs)

    comparisons = []
    names = sorted(texts)
    for left_index, left in enumerate(names):
        left_grams = normalized_ngrams(texts[left])
        for right in names[left_index + 1:]:
            right_grams = normalized_ngrams(texts[right])
            union = left_grams | right_grams
            score = len(left_grams & right_grams) / max(1, len(union))
            comparisons.append(
                {
                    "left": left,
                    "right": right,
                    "char_8gram_jaccard": round(score, 6),
                    "status": "PASS" if score < 0.08 else "REVIEW",
                }
            )
    return comparisons


def validate_distinctiveness_profiles(
    expected_run_ids: list[str],
    profiles: dict[str, dict],
) -> tuple[dict, list[str]]:
    """验证七个设计维度均显式填写，且四个世界在每一维都不重复。"""
    failures = []
    if set(profiles) != set(expected_run_ids):
        failures.append(
            "结构化差异档案 Run 集合不符:"
            f"actual={sorted(profiles)},expected={sorted(expected_run_ids)}"
        )
    dimension_values: dict[str, dict[str, str]] = {}
    for dimension in sorted(DISTINCTIVENESS_KEYS):
        values = {}
        for run_id in expected_run_ids:
            value = (profiles.get(run_id) or {}).get(dimension)
            if not isinstance(value, str) or not value.strip():
                failures.append(f"{run_id} 缺少差异维度:{dimension}")
                continue
            values[run_id] = re.sub(r"\s+", "", value).casefold()
        duplicate_values = {
            value: sorted(run_id for run_id, item in values.items() if item == value)
            for value in set(values.values())
            if sum(item == value for item in values.values()) > 1
        }
        if duplicate_values:
            failures.append(f"差异维度出现重复:{dimension}:{duplicate_values}")
        dimension_values[dimension] = values
    return {
        "status": "PASS" if not failures else "FAIL",
        "dimensions": dimension_values,
    }, failures


def load_release_profile(path: Path) -> tuple[dict, list[str]]:
    """读取并静态校验 release profile。"""
    failures = []
    try:
        profile = read_json(path)
    except Exception as exc:  # noqa: BLE001
        return {}, [f"release profile 无法读取:{exc!r}"]
    expected = profile.get("expected_run_ids")
    reviewed = profile.get("review_required_run_ids")
    roles = profile.get("roles")
    if profile.get("profile_version") != 1:
        failures.append(f"release profile_version 必须为 1:{profile.get('profile_version')!r}")
    if not isinstance(profile.get("batch_id"), str) or not profile.get("batch_id"):
        failures.append("release profile 缺少 batch_id")
    if not isinstance(expected, list) or len(expected) < 2 or len(expected) != len(set(expected)):
        failures.append("expected_run_ids 必须是至少两个不重复 Run")
    if not isinstance(reviewed, list) or not set(reviewed or []).issubset(set(expected or [])):
        failures.append("review_required_run_ids 不是 expected_run_ids 子集")
    if not isinstance(roles, dict) or set(roles) != set(reviewed or []):
        failures.append("roles 必须精确覆盖所有需复审 Run")
    else:
        for run_id, role_map in roles.items():
            if not isinstance(role_map, dict) or set(role_map) != {
                "author", "cross_reviewer", "final_reviewer"
            }:
                failures.append(f"{run_id} 的角色映射不完整")
    if profile.get("batch_id") != CANONICAL_BATCH_ID:
        failures.append("release profile 不是本批次的固定 batch_id")
    if expected != list(CANONICAL_RUN_IDS):
        failures.append("release profile 不是固定四 Run 集合/顺序")
    if reviewed != list(CANONICAL_REVIEW_REQUIRED_RUN_IDS):
        failures.append("release profile 未精确要求三个新 Run 审查链")
    return profile, failures


def main() -> int:
    """解析参数、审计 Run、落盘并输出批次 JSON。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--summary-output", type=Path)
    parser.add_argument(
        "--require-review-chain",
        action="store_true",
        help="额外要求 CROSS_REVIEW、REVISION_LOG 与 FINAL_REVIEW 齐全",
    )
    parser.add_argument(
        "--release-profile",
        type=Path,
        help="启用不可绕过的整批发布验收，并要求精确 Run 集合与角色链",
    )
    args = parser.parse_args()

    release_mode = args.release_profile is not None
    if release_mode and args.summary_output is None:
        parser.error("--release-profile 必须同时提供 --summary-output 持久化唯一批次结论")

    # release 不是一个可由命令行重定义的概念：profile、摘要位置与
    # 四个正式 Run 的文件系统身份都必须与仓库内的固定信任根一致。
    if release_mode:
        canonical_profile_path = ROOT / CANONICAL_RELEASE_PROFILE
        canonical_summary_path = ROOT / CANONICAL_ACCEPTANCE_SUMMARY
        if canonical_profile_path.is_symlink() or not canonical_profile_path.is_file():
            parser.error(f"固定 release profile 缺失或为符号链接:{canonical_profile_path}")
        if args.release_profile.resolve() != canonical_profile_path.resolve():
            parser.error(
                "release mode 只允许固定 profile:"
                f"{CANONICAL_RELEASE_PROFILE.as_posix()}"
            )
        if sha256_file(canonical_profile_path) != CANONICAL_RELEASE_PROFILE_SHA256:
            parser.error("固定 release profile 内容与工具信任根不符")
        if canonical_summary_path.is_symlink():
            parser.error(f"固定验收摘要不得为符号链接:{canonical_summary_path}")
        if args.summary_output.resolve() != canonical_summary_path.resolve():
            parser.error(
                "release mode 只允许固定 summary output:"
                f"{CANONICAL_ACCEPTANCE_SUMMARY.as_posix()}"
            )
        canonical_run_dirs = [ROOT / "output" / "runs" / run_id for run_id in CANONICAL_RUN_IDS]
        if len(args.run_dirs) != len(canonical_run_dirs):
            parser.error("发布必须精确提供四个正式 Run")
        for supplied, canonical in zip(args.run_dirs, canonical_run_dirs):
            if canonical.is_symlink() or not canonical.is_dir():
                parser.error(f"固定 Run 缺失或为符号链接:{canonical}")
            if supplied.resolve() != canonical.resolve():
                parser.error(
                    "release mode 不允许仅 basename 相同的 Run 副本:"
                    f"{supplied} != {canonical}"
                )
        args.release_profile = canonical_profile_path
        args.summary_output = canonical_summary_path
        args.run_dirs = canonical_run_dirs

    release_profile: dict = {}
    release_failures: list[str] = []
    profile_sha256 = None
    if release_mode:
        release_profile, release_failures = load_release_profile(args.release_profile)
        if args.release_profile.is_file():
            profile_sha256 = sha256_file(args.release_profile)

    run_ids = [path.name for path in args.run_dirs]
    if len(run_ids) != len(set(run_ids)):
        release_failures.append(f"输入含重复 Run basename:{run_ids}")
    if release_mode and run_ids != release_profile.get("expected_run_ids"):
        release_failures.append(
            "发布 Run 必须按 profile 精确一次性给齐:"
            f"actual={run_ids},expected={release_profile.get('expected_run_ids')}"
        )

    audited: list[tuple[Path, dict]] = []
    for run_dir in args.run_dirs:
        run_id = run_dir.name
        reviewed_ids = set(release_profile.get("review_required_run_ids") or [])
        needs_review = args.require_review_chain or (release_mode and run_id in reviewed_ids)
        expected_roles = (release_profile.get("roles") or {}).get(run_id)
        result = audit_run(
            run_dir,
            require_review_chain=needs_review,
            expected_roles=expected_roles,
            release_mode=release_mode,
        )
        audited.append((run_dir, result))

    comparisons = distinctiveness(audited)
    if release_mode:
        expected_pairs = len(run_ids) * (len(run_ids) - 1) // 2
        if len(comparisons) != expected_pairs:
            release_failures.append(
                f"横向比较数量不足:{len(comparisons)}!={expected_pairs}"
            )
        structured_distinctiveness, structured_failures = validate_distinctiveness_profiles(
            release_profile.get("expected_run_ids") or [],
            release_profile.get("distinctiveness_profiles") or {},
        )
        release_failures.extend(structured_failures)
    else:
        structured_distinctiveness = None

    batch_pass = (
        not release_failures
        and all(result["status"] == "PASS" for _, result in audited)
        and all(item["status"] == "PASS" for item in comparisons)
    )
    batch_id = release_profile.get("batch_id") if release_mode else None
    for _, result in audited:
        result["batch"] = {
            "batch_id": batch_id,
            "batch_status": "PASS" if batch_pass else "FAIL",
            "release_mode": release_mode,
            "release_profile_sha256": profile_sha256,
        }
    summary = {
        "schema_version": 2,
        "batch_id": batch_id,
        "status": "PASS" if batch_pass else "FAIL",
        "release_mode": release_mode,
        "release_profile": (
            CANONICAL_RELEASE_PROFILE.as_posix() if release_mode else None
        ),
        "release_profile_sha256": profile_sha256,
        "release_failures": release_failures,
        "runs": [result for _, result in audited],
        "distinctiveness": comparisons,
        "structured_distinctiveness": structured_distinctiveness,
        "tool_hashes": (
            {
                path.relative_to(ROOT).as_posix(): sha256_file(path)
                for path in [
                    Path(__file__).resolve(),
                    ROOT / "tools/showcase_integrity.py",
                    ROOT / "tools/audit_run.py",
                    ROOT / "eval/judge.py",
                ]
            }
            if release_mode
            else {}
        ),
    }
    rendered = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    if args.summary_output:
        write_json_atomic(args.summary_output, summary)

    # 单 Run 诊断永远写 RUN_AUDIT；只有整批发布 PASS 才签 FINAL_ACCEPTANCE。
    for run_dir, result in audited:
        report_name = "FINAL_ACCEPTANCE.json" if release_mode and batch_pass else "RUN_AUDIT.json"
        write_json_atomic(run_dir / "production" / report_name, result)
    print(rendered, end="")
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
