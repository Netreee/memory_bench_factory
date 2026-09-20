"""工厂评测输入的只读校验和指纹。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import BenchmarkRef, ConfigurationError


REQUIRED_FILES = ("00_about.json", "05_corpus.json", "06_grounded_questions.json")


@dataclass(frozen=True)
class BenchmarkInspection:
    path: Path
    scenario: str
    factory_run_id: str
    factory_status: str
    met_status: str | None
    n_sessions: int
    n_docs: int
    n_questions: int
    files: dict[str, str]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "scenario": self.scenario,
            "factory_run_id": self.factory_run_id,
            "factory_status": self.factory_status,
            "met_status": self.met_status,
            "n_sessions": self.n_sessions,
            "n_docs": self.n_docs,
            "n_questions": self.n_questions,
            "files": self.files,
            "warnings": list(self.warnings),
        }


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"benchmark 缺少文件: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"benchmark JSON 无法解析: {path}: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_mapping(value: Any, location: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{location} 必须是 object")
    return value


def _require_nonempty_text(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{location} 必须是非空字符串")
    return value


def _validate_about(value: Any) -> None:
    about = _require_mapping(value, "00_about.json")
    protocol = _require_mapping(
        about.get("answer_protocol"), "00_about.json.answer_protocol"
    )
    rules = protocol.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ConfigurationError("00_about.json.answer_protocol.rules 必须是非空数组")
    for index, rule in enumerate(rules):
        _require_nonempty_text(rule, f"00_about.json.answer_protocol.rules[{index}]")
    sentinel_map = protocol.get("gold_sentinel_map", {})
    if not isinstance(sentinel_map, dict):
        raise ConfigurationError(
            "00_about.json.answer_protocol.gold_sentinel_map 必须是 object"
        )


def _validate_corpus(value: Any) -> tuple[list[dict], set[int]]:
    root = _require_mapping(value, "05_corpus.json")
    inner = root.get("corpus", root)
    inner = _require_mapping(inner, "05_corpus.json.corpus")
    sessions = inner.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        raise ConfigurationError("05_corpus.json.corpus.sessions 必须是非空数组")
    session_ids: set[int] = set()
    doc_ids: set[str] = set()
    for session_index, raw_session in enumerate(sessions):
        location = f"05_corpus.json.corpus.sessions[{session_index}]"
        session = _require_mapping(raw_session, location)
        try:
            session_id = int(session["session_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigurationError(f"{location}.session_id 必须是整数") from exc
        if session_id in session_ids:
            raise ConfigurationError(f"{location}.session_id 重复: {session_id}")
        session_ids.add(session_id)
        _require_nonempty_text(session.get("date"), f"{location}.date")
        docs = session.get("docs")
        if not isinstance(docs, list):
            raise ConfigurationError(f"{location}.docs 必须是数组")
        for doc_index, raw_doc in enumerate(docs):
            doc_location = f"{location}.docs[{doc_index}]"
            doc = _require_mapping(raw_doc, doc_location)
            doc_id = _require_nonempty_text(doc.get("doc_id"), f"{doc_location}.doc_id")
            if doc_id in doc_ids:
                raise ConfigurationError(f"{doc_location}.doc_id 重复: {doc_id}")
            doc_ids.add(doc_id)
            _require_nonempty_text(doc.get("type"), f"{doc_location}.type")
            _require_nonempty_text(doc.get("content"), f"{doc_location}.content")
            fact_refs = doc.get("fact_refs", [])
            if not isinstance(fact_refs, list) or not all(
                isinstance(item, str) for item in fact_refs
            ):
                raise ConfigurationError(f"{doc_location}.fact_refs 必须是字符串数组")
    return sessions, session_ids


def _validate_questions(value: Any, session_ids: set[int]) -> tuple[list[dict], list[str]]:
    questions = (
        value
        if isinstance(value, list)
        else value.get("questions") if isinstance(value, dict) else None
    )
    if not isinstance(questions, list) or not questions:
        raise ConfigurationError("06_grounded_questions.json.questions 必须是非空数组")
    warnings: list[str] = []
    explicit_ids: set[str] = set()
    missing_explicit_id = 0
    for index, raw_question in enumerate(questions):
        location = f"06_grounded_questions.json.questions[{index}]"
        question = _require_mapping(raw_question, location)
        _require_nonempty_text(question.get("question"), f"{location}.question")
        _require_nonempty_text(question.get("line"), f"{location}.line")
        _require_nonempty_text(question.get("capability"), f"{location}.capability")
        if "gt" not in question:
            raise ConfigurationError(f"{location}.gt 缺失")
        question_id = question.get("question_id")
        if question_id is None:
            missing_explicit_id += 1
        else:
            question_id = _require_nonempty_text(question_id, f"{location}.question_id")
            if question_id in explicit_ids:
                raise ConfigurationError(f"{location}.question_id 重复: {question_id}")
            explicit_ids.add(question_id)
        evidence = question.get("evidence_sessions", [])
        if not isinstance(evidence, list):
            raise ConfigurationError(f"{location}.evidence_sessions 必须是数组")
        for raw_session_id in evidence:
            try:
                session_id = int(raw_session_id)
            except (TypeError, ValueError) as exc:
                raise ConfigurationError(
                    f"{location}.evidence_sessions 包含非整数值"
                ) from exc
            if session_id not in session_ids:
                raise ConfigurationError(
                    f"{location}.evidence_sessions 引用了不存在的 session_id={session_id}"
                )
    if missing_explicit_id:
        warnings.append(
            f"{missing_explicit_id}/{len(questions)} 道题缺少稳定 question_id；"
            "运行器只能使用内容 hash 作为临时 ID"
        )
    return questions, warnings


def inspect_benchmark(ref: BenchmarkRef) -> BenchmarkInspection:
    root = ref.path
    if not root.is_dir():
        raise ConfigurationError(f"benchmark 目录不存在: {root}")
    paths = {name: root / name for name in REQUIRED_FILES}
    for path in paths.values():
        if not path.is_file():
            raise ConfigurationError(f"benchmark 缺少 {path.name}: {root}")

    _validate_about(_load_json(paths["00_about.json"]))
    sessions, session_ids = _validate_corpus(_load_json(paths["05_corpus.json"]))
    questions_obj = _load_json(paths["06_grounded_questions.json"])
    questions, schema_warnings = _validate_questions(questions_obj, session_ids)

    manifest_path = root / "manifest.json"
    manifest = _load_json(manifest_path) if manifest_path.is_file() else {}
    validated_path = root / "validated.json"
    validated = _load_json(validated_path) if validated_path.is_file() else {}
    algo = manifest.get("algo") if isinstance(manifest.get("algo"), dict) else {}
    raw_met_status = validated.get("met_status") or algo.get("met_status")
    met_status = str(raw_met_status) if raw_met_status else None
    warnings: list[str] = list(schema_warnings)
    if met_status and not met_status.startswith("MET"):
        warnings.append(f"factory release gate 未满足: {met_status}")
        if not ref.allow_unmet:
            raise ConfigurationError(
                f"benchmark 是 {met_status}；仅诊断实验可显式设置 benchmark.allow_unmet=true"
            )
    if validated_path.is_file() and ref.corpus_variant == "full":
        raise ConfigurationError("当前目录是 filtered/validated 产物，但 corpus_variant 声明为 full")
    if not validated_path.is_file() and ref.corpus_variant == "filtered":
        warnings.append("corpus_variant=filtered，但目录没有 validated.json，无法证明过滤 lineage")

    scenario = str(validated.get("scenario") or manifest.get("scenario") or root.name.split("__", 1)[0])
    run_id = str(manifest.get("run_id") or validated.get("source_run") or root.name)
    return BenchmarkInspection(
        path=root,
        scenario=scenario,
        factory_run_id=run_id,
        factory_status=str(manifest.get("status") or "unknown"),
        met_status=met_status,
        n_sessions=len(sessions),
        n_docs=sum(len(s.get("docs") or []) for s in sessions if isinstance(s, dict)),
        n_questions=len(questions),
        files={name: _sha256(path) for name, path in paths.items()},
        warnings=tuple(warnings),
    )
