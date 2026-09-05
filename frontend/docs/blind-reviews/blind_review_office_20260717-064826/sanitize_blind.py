#!/usr/bin/env python3
"""生成供独立盲审使用的冻结成品，并记录原始与脱敏文件哈希。

输入是单个完整 run 目录，输出仅包含审稿人判断成品质量所需的公开语义。
脚本不会改写原始 run；结构化生成元信息会被剥离，但正文中的真实缺陷原样保留。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


TYPE_MAP = {
    "IE": "指定时点事实查询",
    "KU": "最新状态查询",
    "TR": "时序变化点",
    "MR": "跨期聚合统计",
    "FORGET": "已停止统计信息",
    "PREEXPIRE": "停统前最后值",
    "L2_multihop": "多跳关系推理",
    "L3_order": "跨事件排序",
    "L4_preference": "跨期偏好归纳",
    "L5_conflict": "冲突来源裁决",
    "L6_refusal": "无据拒答",
    "L7_consolidation": "整体趋势归纳",
    "L9_induction": "隐式规则归纳",
    "L10_admission": "敏感信息不复述",
    "L10_witness": "非敏感见证读取",
}


def load_json(path: Path) -> Any:
    """读取 UTF-8 JSON 文件并返回对象。"""

    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, value: Any) -> None:
    """稳定写出 UTF-8 JSON，便于后续哈希与复核。"""

    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def sha256(path: Path) -> str:
    """计算文件 SHA256，用于冻结被审 artifact。"""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def clean_gold(gold: Any) -> Any:
    """保留答案的公开语义，删除内部时点指针、操作码和构造标记。"""

    if isinstance(gold, list):
        public_keys = ("field", "value", "date")
        return [
            {key: event[key] for key in public_keys if key in event}
            if isinstance(event, dict)
            else event
            for event in gold
        ]
    if isinstance(gold, dict):
        if gold.get("forgotten") is True:
            return "已停止统计/不再跟踪"
        if "value" in gold:
            return gold["value"]
        public_keys = ("week", "date", "from", "to", "ordinal")
        return {key: gold[key] for key in public_keys if key in gold}
    if gold == "INSUFFICIENT_EVIDENCE":
        return "无此项/查无此记录"
    if gold == "SENSITIVE_WITHHELD":
        return "应拒绝逐字复述敏感信息"
    return gold


def clean_protocol(about: dict[str, Any]) -> dict[str, Any]:
    """公开答题契约的自然语言部分，去掉内部产线名和 sentinel 名。"""

    protocol = about.get("answer_protocol") or {}
    rules: list[str] = []
    for rule in protocol.get("rules") or []:
        text = re.sub(r"\(gold 标记 INSUFFICIENT,产线 ABS\)", "", rule)
        text = re.sub(r"\(gold forgotten=True,产线 FORGET\)", "", text)
        text = re.sub(r"\(产线 PREEXPIRE\)", "", text)
        rules.append(text)
    return {
        "answer_protocol": {
            "version": protocol.get("version"),
            "rules": rules,
        }
    }


def main() -> None:
    """从 run 生成三个盲审文件及可复现 manifest。"""

    if len(sys.argv) != 3:
        raise SystemExit("usage: sanitize_blind.py RUN_DIR OUT_DIR")
    run_dir = Path(sys.argv[1]).resolve()
    out_dir = Path(sys.argv[2]).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    qa_path = run_dir / "06_grounded_questions.json"
    corpus_path = run_dir / "05_corpus.json"
    about_path = run_dir / "00_about.json"
    questions = load_json(qa_path)
    if isinstance(questions, dict):
        questions = questions.get("questions") or []

    reviewed_qa = []
    for index, item in enumerate(questions, 1):
        reviewed_qa.append(
            {
                "qid": f"Q{index:03d}",
                "claimed_type": TYPE_MAP.get(item.get("capability"), "其他"),
                "question": item.get("question"),
                "gold": clean_gold(item.get("gt")),
                "evidence_sessions": item.get("evidence_sessions") or [],
            }
        )

    corpus_root = load_json(corpus_path)
    corpus_root = corpus_root.get("corpus", corpus_root)
    sessions = corpus_root.get("sessions", corpus_root) if isinstance(corpus_root, dict) else corpus_root
    reviewed_sessions = [
        {
            "session_id": session.get("session_id"),
            "date": session.get("date"),
            "docs": [
                {
                    "doc_id": document.get("doc_id"),
                    "type": document.get("type"),
                    "content": document.get("content"),
                }
                for document in session.get("docs") or []
            ],
        }
        for session in sessions
    ]

    reviewed_corpus_path = out_dir / "reviewed_corpus.json"
    reviewed_qa_path = out_dir / "reviewed_qa.json"
    disclosed_about_path = out_dir / "disclosed_about.json"
    dump_json(reviewed_corpus_path, {"sessions": reviewed_sessions})
    dump_json(reviewed_qa_path, reviewed_qa)
    dump_json(disclosed_about_path, clean_protocol(load_json(about_path)))

    manifest = {
        "artifact_id": run_dir.name,
        "source_files": {
            name: {"sha256": sha256(run_dir / name), "bytes": (run_dir / name).stat().st_size}
            for name in ("00_about.json", "05_corpus.json", "06_grounded_questions.json")
        },
        "reviewed_files": {
            path.name: {"sha256": sha256(path), "bytes": path.stat().st_size}
            for path in (reviewed_corpus_path, reviewed_qa_path, disclosed_about_path)
        },
        "counts": {
            "sessions": len(reviewed_sessions),
            "documents": sum(len(session["docs"]) for session in reviewed_sessions),
            "questions": len(reviewed_qa),
        },
    }
    dump_json(out_dir / "review_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
