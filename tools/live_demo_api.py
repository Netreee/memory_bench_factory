"""Memory Forge 本地展示 API：启动真实 Run，并只返回脱敏后的观测数据。"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.quality import quality_snapshot

RUNS_DIR = ROOT / "output" / "runs"
DEFAULT_ALLOWED_ORIGINS = ["http://127.0.0.1:3000", "http://localhost:3000"]
STAGE_ORDER = ["input", "whitepaper", "world", "orders", "well_posed", "questions", "corpus", "grounding", "quality"]
STAGE_ARTIFACTS = {
    "input": "00_input.json",
    "whitepaper": "01_whitepaper.json",
    "world": "02_world.json",
    "orders": "03_orders.json",
    "well_posed": "03_well_posed_report.json",
    "questions": "04_questions.json",
    "corpus": "05_corpus.json",
    "grounding": "06_grounded_questions.json",
    "quality": "07_release.json",
}
STEP_LABELS = {
    "council.observe": "议会正在理解样例",
    "council.skeptic": "怀疑者正在寻找边界",
    "council.map": "架构师正在映射能力",
    "council.medium": "媒介专家正在设计证据载体",
    "council.style": "文风专家正在建立语言约束",
    "council.traps": "陷阱专家正在注入干扰",
    "council.critique": "审查者正在校验白皮书",
    "council.world": "世界架构师正在编织故事契约",
    "council.world_review": "世界审稿人正在闭合因果链",
    "world.batch": "正在生成世界实体",
    "world.structure": "正在编织世界关系",
    "world.repair": "机械校验正在修复世界",
    "phrase": "正在铸造题面",
    "render.signal": "正在渲染信号文档",
    "render.discriminate": "正在检查文档可辨识性",
    "render.filler": "正在铺设记忆草堆",
    "render.conflict": "正在渲染冲突证据",
}
STEP_STAGE = {
    "council": "whitepaper",
    "world": "world",
    "phrase": "questions",
    "render": "corpus",
}
COUNCIL = [
    ("council.observe", "OBSERVER", "场景观测"),
    ("council.skeptic", "SKEPTIC", "边界质疑"),
    ("council.map", "ARCHITECT", "能力映射"),
    ("council.medium", "MEDIUM", "媒介设计"),
    ("council.style", "STYLIST", "文风约束"),
    ("council.traps", "TRAPSMITH", "陷阱注入"),
    ("council.critique", "CRITIC", "机械审查"),
]


def _allowed_origins() -> list[str]:
    """读取逗号分隔的前端来源；未配置时仅允许本机演示页。"""
    configured = os.getenv("MEMORY_FORGE_ALLOWED_ORIGINS", "")
    origins = [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]
    return origins or DEFAULT_ALLOWED_ORIGINS


class RunRequest(BaseModel):
    """浏览器提交的一条最小演示任务。"""

    scenario_text: str = Field(min_length=12, max_length=1200)
    sample_name: str = Field(min_length=1, max_length=180)
    sample_text: str = Field(min_length=1, max_length=200 * 1024)

    @field_validator("scenario_text", "sample_text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("内容不能为空")
        return value.strip()

    @field_validator("sample_name")
    @classmethod
    def txt_only(cls, value: str) -> str:
        name = Path(value).name
        if not name.lower().endswith(".txt"):
            raise ValueError("演示版只接收 .txt 文件")
        return name


@dataclass
class Job:
    process: subprocess.Popen
    created_at: float
    cancelled: bool = False


app = FastAPI(title="Memory Forge Live Demo", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

JOBS: dict[str, Job] = {}
JOB_LOCK = threading.Lock()


def _live_configuration() -> tuple[bool, str]:
    """只检查 Live 所需字段是否存在，不加载客户端，也不返回任何秘密值。"""
    file_values = dotenv_values(ROOT / ".env") if (ROOT / ".env").exists() else {}
    # Match load_dotenv(override=False): an explicit empty environment value is not replaced.
    api_key = os.environ.get("OPENAI_API_KEY", file_values.get("OPENAI_API_KEY"))
    model = os.environ.get("MODEL", file_values.get("MODEL"))
    configured = bool(
        api_key
        and model
        and not str(api_key).startswith("replace-with-")
        and not str(model).startswith("replace-with-")
    )
    return configured, str(model or "not-configured")


def _normalize_run_id(value: str) -> str:
    """只接受本项目生成的简单 Run ID，拒绝任何路径片段。"""
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,96}", value):
        raise HTTPException(status_code=400, detail="非法 Run ID")
    return value


def _read_json(path: Path, fallback: Any = None) -> Any:
    """容忍流水线正在覆写 JSON，读取失败时返回 fallback。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return fallback


def _atomic_json(path: Path, value: Any) -> None:
    """原子写入服务侧状态，避免前端轮询读到半截 JSON。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _safe_text(value: Any, limit: int = 180) -> str:
    """清除异常中的 URL、令牌形态和多余换行。"""
    text = str(value or "").replace("\n", " ")[:limit]
    text = re.sub(r"https?://\S+", "[endpoint]", text)
    text = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[redacted]", text)
    text = re.sub(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]?\s*\S+", r"\1=[redacted]", text)
    return text


def _prompt_summary(run_dir: Path) -> dict:
    """只读取 tracer 元数据；system/user 永远不离开后端。"""
    calls: list[dict] = []
    questions: list[dict] = []
    path = run_dir / "prompts.jsonl"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        step = str(item.get("step") or "")
        calls.append({
            "i": int(item.get("i") or len(calls) + 1),
            "ts": float(item.get("ts") or 0),
            "latency_ms": int(item.get("latency_ms") or 0),
            "ok": bool(item.get("ok")),
            "step": step,
            "label": STEP_LABELS.get(step, step or "流水线调用"),
        })
        if step == "phrase" and item.get("ok"):
            try:
                parsed = json.loads(item.get("out_preview") or "{}")
                question = _safe_text(parsed.get("question"), 240)
                if question:
                    questions.append({"question": question, "source": "live-call", "i": item.get("i")})
            except (TypeError, json.JSONDecodeError):
                pass
    counts = Counter(call["step"] for call in calls)
    return {
        "count": len(calls),
        "errors": sum(1 for call in calls if not call["ok"]),
        "step_counts": dict(counts),
        "recent_calls": calls[-8:],
        "live_questions": questions[-12:],
        "last_ts": calls[-1]["ts"] if calls else None,
    }


def _artifact_views(run_dir: Path, prompt: dict) -> dict:
    """从各阶段产物提取允许展示的聚合字段和安全样例。"""
    input_obj = _read_json(run_dir / "00_input.json", {}) or {}
    whitepaper = _read_json(run_dir / "01_whitepaper.json", {}) or {}
    world = _read_json(run_dir / "02_world.json", {}) or {}
    questions_obj = _read_json(run_dir / "04_questions.json", []) or []
    corpus_obj = _read_json(run_dir / "05_corpus.json", {}) or {}
    grounded_obj = _read_json(run_dir / "06_grounded_questions.json", []) or []
    grounding_report = _read_json(run_dir / "06_grounding_report.json", {}) or {}

    entities = world.get("entities") or {}
    orders_obj = _read_json(run_dir / "03_orders.json", None)
    well_report = _read_json(run_dir / "03_well_posed_report.json", None)
    artifact_counts = {
        "entities": len(entities) if (run_dir / "02_world.json").is_file() and "entities" in world else None,
        "sessions": world.get("n_sessions"),
        "events": len(world["events"]) if isinstance(world.get("events"), list) else None,
        "orders": len(orders_obj) if isinstance(orders_obj, list) else None,
        "questions": len(questions_obj) if isinstance(_read_json(run_dir / "04_questions.json"), list) else None,
    }
    if isinstance(entities, dict):
        entity_names = [_safe_text(name, 32) for name in list(entities)[:12]]
    elif isinstance(entities, list):
        entity_names = [_safe_text(item.get("name") or item.get("id"), 32) for item in entities[:12] if isinstance(item, dict)]
    else:
        entity_names = []

    corpus = corpus_obj.get("corpus", corpus_obj) if isinstance(corpus_obj, dict) else {}
    sessions = corpus.get("sessions", []) if isinstance(corpus, dict) else []
    if isinstance(_read_json(run_dir / "05_corpus.json"), dict) and "sessions" in corpus:
        docs = [doc for session in sessions if isinstance(session, dict)
                for doc in (session.get("docs") or []) if isinstance(doc, dict)]
        artifact_counts["docs"] = len(docs)
        artifact_counts["chars"] = sum(len(str(doc.get("content") or "")) for doc in docs)
    session_views = []
    for session in sessions[-10:]:
        if not isinstance(session, dict):
            continue
        docs = session.get("docs") or []
        session_views.append({
            "id": _safe_text(session.get("session_id"), 30),
            "date": _safe_text(session.get("date"), 30),
            "docs": len(docs),
            "types": list(dict.fromkeys(_safe_text(doc.get("type"), 28) for doc in docs if isinstance(doc, dict)))[:5],
        })

    if questions_obj:
        question_views = [{
            "question": _safe_text(item.get("question"), 240),
            "line": _safe_text(item.get("line"), 40),
            "capability": _safe_text(item.get("capability"), 40),
            "source": "artifact",
        } for item in questions_obj[-12:] if isinstance(item, dict) and item.get("question")]
    else:
        question_views = prompt["live_questions"]

    grounded_views = [{
        "question": _safe_text(item.get("question"), 240),
        "line": _safe_text(item.get("line"), 40),
        "capability": _safe_text(item.get("capability"), 40),
    } for item in grounded_obj[-8:] if isinstance(item, dict) and item.get("question")]

    profile = whitepaper.get("domain_profile") or {}
    story = whitepaper.get("story_contract") or {}
    style = whitepaper.get("style_spec") or {}
    capability_targets = whitepaper.get("capability_targets") or {}
    source_authority = whitepaper.get("source_authority") or []
    active_lines = [
        _safe_text(item.get("line"), 40)
        for item in (whitepaper.get("active_lines") or [])
        if isinstance(item, dict) and float(item.get("weight") or 0) > 0
    ]
    few_shot = input_obj.get("few_shot") or []
    return {
        "counts": artifact_counts,
        "well_posed": (well_report or {}).get("overall", {}) if isinstance(well_report, dict) else {},
        "input": {
            "description": _safe_text(input_obj.get("description"), 420),
            "sample_name": _safe_text(few_shot[0].get("title"), 120) if few_shot else "",
            "sample_chars": len(str(few_shot[0].get("content") or "")) if few_shot else 0,
        },
        "whitepaper": {
            "title": _safe_text(whitepaper.get("title"), 100),
            "scenario_id": _safe_text(whitepaper.get("scenario_id"), 80),
            "entity_noun": _safe_text(profile.get("entity_noun"), 40),
            "protagonist": _safe_text(story.get("protagonist"), 60),
            "story_arc": _safe_text(story.get("arc"), 260),
            "central_paradox": _safe_text(story.get("central_paradox"), 260),
            "irreversible_cost": _safe_text(story.get("irreversible_cost"), 260),
            "tone": _safe_text(style.get("tone"), 180),
            "format": _safe_text(style.get("format"), 180),
            "target_questions": capability_targets.get("total_q"),
            "target_star_questions": capability_targets.get("star_questions"),
            "source_tiers": [
                _safe_text(item.get("meaning"), 100)
                for item in source_authority[:4]
                if isinstance(item, dict) and item.get("meaning")
            ],
            "doc_genres": [_safe_text(value, 48) for value in (profile.get("doc_genres") or [])[:6]],
            "active_lines": active_lines,
        },
        "world": {
            "entity_names": entity_names,
            "n_sessions": world.get("n_sessions"),
            "event_count": artifact_counts["events"],
        },
        "questions": question_views,
        "corpus_sessions": session_views,
        "grounding": {
            "overall": (grounding_report.get("overall") or {}) if isinstance(grounding_report, dict) else {},
            "questions": grounded_views,
        },
    }


def _known_count(value: Any) -> int | None:
    """Unknown or malformed measurements stay unknown; observed zero remains zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0 or not float(value).is_integer():
        return None
    return int(value)


def _safe_algo(manifest: dict, views: dict, prompt: dict) -> dict:
    """Only report observed totals, never preview sizes or LLM attempt counts."""
    algo = manifest.get("algo") or {}
    counts = views.get("counts") or {}
    well = (algo.get("well_posed") or {}).get("overall") or views.get("well_posed") or {}
    grounding = (algo.get("grounding") or {}).get("overall") or views["grounding"]["overall"]
    quality = algo.get("quality") or {}
    def count(key: str) -> int | None:
        observed = _known_count(counts.get(key))
        return observed if observed is not None else _known_count(algo.get(key))
    return {
        **{key: count(key) for key in ("entities", "sessions", "events", "orders", "questions", "docs", "chars")},
        "well_posed": {"n": _known_count(well.get("n")), "kept": _known_count(well.get("well_posed")), "rate": well.get("pass_rate")},
        "star_questions": _known_count(quality.get("star_questions")),
        "signal_docs": _known_count(quality.get("signal_documents")),
        "continuity_conflicts": _known_count(quality.get("unintended_continuity_conflicts")),
        "grounding": {"n": _known_count(grounding.get("n")), "grounded": _known_count(grounding.get("grounded")), "survival": grounding.get("survival")},
    }


def _run_snapshot(run_id: str, *, recorded: bool = False) -> dict:
    """组装一个前端可直接消费、且不含秘密字段的 Run 快照。"""
    run_id = _normalize_run_id(run_id)
    run_dir = RUNS_DIR / run_id
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="Run 不存在")

    manifest = _read_json(run_dir / "manifest.json", {}) or {}
    overlay = _read_json(run_dir / ".live_state.json", {}) or {}
    prompt = _prompt_summary(run_dir)
    views = _artifact_views(run_dir, prompt)
    stage_meta = manifest.get("stages") or {}
    current = str(manifest.get("current_stage") or "")
    raw_status = str(overlay.get("status") or manifest.get("status") or "starting")
    status = {"done": "succeeded", "manual_failed": "failed", "error": "failed"}.get(raw_status, raw_status)
    if status not in {"starting", "running", "succeeded", "failed", "cancelled"}:
        status = "unknown"

    with JOB_LOCK:
        job = JOBS.get(run_id)
    if job and job.cancelled:
        status = "cancelled"
    elif job and job.process.poll() is not None and status in {"starting", "running"}:
        status = "failed"

    quality = quality_snapshot(run_dir)
    generation_complete = all((stage_meta.get(name) or {}).get("done")
                              for name in STAGE_ORDER if name != "quality")
    generation_status = "succeeded" if generation_complete or status == "succeeded" else status

    stages = []
    current_index = STAGE_ORDER.index(current) if current in STAGE_ORDER else -1
    for index, name in enumerate(STAGE_ORDER):
        meta = stage_meta.get(name) or {}
        if meta.get("done"):
            stage_status = "succeeded"
        elif status == "failed" and name == current:
            stage_status = "failed"
        elif status == "cancelled" and name == current:
            stage_status = "cancelled"
        elif name == current and status in {"running", "starting"}:
            stage_status = "running"
        else:
            stage_status = "pending"
        stages.append({
            "name": name,
            "index": index,
            "status": stage_status,
            "elapsed_s": meta.get("elapsed_s"),
            "artifact": STAGE_ARTIFACTS[name],
            "ready": (run_dir / STAGE_ARTIFACTS[name]).exists(),
        })

    error = _read_json(run_dir / ".live_error.json", {}) or {}
    created_ts = min((meta.get("started_ts") for meta in stage_meta.values() if meta.get("started_ts")), default=None)
    elapsed_s = int(time.time() - created_ts) if created_ts and status in {"running", "starting"} else int(sum(float((meta or {}).get("elapsed_s") or 0) for meta in stage_meta.values()))
    last_ts = prompt["last_ts"]
    agents = [{
        "id": step,
        "name": name,
        "role": role,
        "status": "complete" if prompt["step_counts"].get(step) else "active" if current == "whitepaper" else "waiting",
    } for step, name, role in COUNCIL]

    return {
        "run_id": run_id,
        "source": "recorded" if recorded else "live",
        "status": status,
        "execution_status": status,
        "generation_status": generation_status,
        "recorded_status": raw_status,
        "quality": quality,
        "eligible": quality["eligible"],
        "current_stage": current,
        "current_index": current_index,
        "created": manifest.get("created"),
        "elapsed_s": elapsed_s,
        "stall_s": int(time.time() - last_ts) if last_ts and status == "running" else None,
        "llm_calls": prompt["count"],
        "llm_errors": prompt["errors"],
        "step_now": prompt["recent_calls"][-1]["label"] if prompt["recent_calls"] else ("初始化本地任务" if status == "starting" else "等待新事件"),
        "stages": stages,
        "metrics": _safe_algo(manifest, views, prompt),
        "agents": agents,
        "recent_calls": prompt["recent_calls"],
        "views": views,
        "error": {"type": _safe_text(error.get("type"), 80), "message": _safe_text(error.get("message"), 260)} if error else None,
        "output": "06_grounded_questions.json",
    }


def _active_job() -> tuple[str, Job] | None:
    with JOB_LOCK:
        for run_id, job in JOBS.items():
            if job.process.poll() is None and not job.cancelled:
                return run_id, job
    return None


def _stage_for_step(step: str) -> str:
    """把 tracer step 归到 00–06 的真实阶段。"""
    prefix = step.split(".", 1)[0]
    return STEP_STAGE.get(prefix, "questions" if step == "phrase" else "")


def _replay_events(run_dir: Path, manifest: dict) -> tuple[list[dict], float]:
    """从已有 Run 重建 60 秒时间线，并给瞬时机械阶段保留可见时间。"""
    stage_rows: list[dict] = []
    for stage in STAGE_ORDER:
        meta = (manifest.get("stages") or {}).get(stage) or {}
        started = meta.get("started_ts")
        if not started:
            continue
        elapsed = max(0.0, float(meta.get("elapsed_s") or 0))
        stage_rows.append({"stage": stage, "started": float(started), "elapsed": elapsed, "done": bool(meta.get("done"))})

    if not stage_rows:
        return [], 0
    replay_duration_ms = 60_000
    base_slot_ms = 2_000
    flexible_ms = max(0, replay_duration_ms - base_slot_ms * len(stage_rows))
    weight_sum = sum(max(row["elapsed"], 0.1) ** 0.5 for row in stage_rows)
    cursor = 0.0
    slots: dict[str, dict] = {}
    for index, row in enumerate(stage_rows):
        weight = max(row["elapsed"], 0.1) ** 0.5
        duration = base_slot_ms + flexible_ms * weight / weight_sum
        if index == len(stage_rows) - 1:
            duration = replay_duration_ms - cursor
        slots[row["stage"]] = {**row, "at_start": round(cursor), "at_end": round(cursor + duration)}
        cursor += duration

    events: list[dict] = []
    for row in stage_rows:
        slot = slots[row["stage"]]
        events.append({"at_ms": slot["at_start"], "type": "stage_started", "stage": row["stage"]})
        if row["done"]:
            events.append({"at_ms": slot["at_end"], "type": "stage_completed", "stage": row["stage"]})

    path = run_dir / "prompts.jsonl"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        step = str(item.get("step") or "")
        stage = _stage_for_step(step)
        slot = slots.get(stage)
        if not slot:
            continue
        source_ts = float(item.get("ts") or 0)
        source_span = max(float(slot["elapsed"]), 0.1)
        ratio = min(1.0, max(0.0, (source_ts - float(slot["started"])) / source_span))
        # 在阶段槽的中间 84% 安排调用，保留清楚的入场和收束镜头。
        at_ms = round(slot["at_start"] + (0.08 + ratio * 0.84) * (slot["at_end"] - slot["at_start"]))
        event = {
            "at_ms": at_ms,
            "type": "call",
            "stage": stage,
            "i": int(item.get("i") or 0),
            "step": step,
            "label": STEP_LABELS.get(step, step or "流水线调用"),
            "ok": bool(item.get("ok")),
            "latency_ms": int(item.get("latency_ms") or 0),
        }
        if step == "phrase" and item.get("ok"):
            try:
                question = json.loads(item.get("out_preview") or "{}").get("question")
                if question:
                    event["question"] = _safe_text(question, 240)
            except (AttributeError, TypeError, json.JSONDecodeError):
                pass
        events.append(event)

    events.sort(key=lambda event: (event["at_ms"], 0 if event["type"] == "stage_started" else 2 if event["type"] == "stage_completed" else 1))
    for seq, event in enumerate(events, 1):
        event["seq"] = seq
    original_start = min(row["started"] for row in stage_rows)
    original_end = max(row["started"] + row["elapsed"] for row in stage_rows)
    original_duration = max(0.1, original_end - original_start)
    return events, original_duration


def _terminate_job(run_id: str, job: Job) -> None:
    """终止 worker 的整个进程组，并持久化取消态。"""
    job.cancelled = True
    if job.process.poll() is None:
        for sig, timeout in ((signal.SIGINT, 4), (signal.SIGTERM, 2), (signal.SIGKILL, 1)):
            try:
                os.killpg(job.process.pid, sig)
                job.process.wait(timeout=timeout)
                break
            except ProcessLookupError:
                break
            except subprocess.TimeoutExpired:
                continue
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(run_dir / ".live_state.json", {"status": "cancelled", "cancelled_at": time.time()})
    manifest_path = run_dir / "manifest.json"
    manifest = _read_json(manifest_path, {}) or {}
    if manifest:
        manifest["status"] = "cancelled"
        _atomic_json(manifest_path, manifest)


@app.get("/api/health")
def health() -> dict:
    """区分回放服务与真实 Live 是否就绪，且绝不在健康检查时加载密钥。"""
    live_ready, model = _live_configuration()
    return {
        "ok": True,
        "live_ready": live_ready,
        "engine": "memory-forge-local",
        "model": model,
        "stop_at": "07_quality",
    }


@app.get("/api/runs")
def replayable_runs() -> dict:
    """列出全部有 manifest 的历史 Run；精选七场景优先，其余保留归档状态。"""
    rows = []
    if RUNS_DIR.exists():
        for run_dir in sorted(RUNS_DIR.iterdir(), reverse=True):
            manifest = _read_json(run_dir / "manifest.json", {}) or {}
            if not manifest:
                continue
            stages = manifest.get("stages") or {}
            rows.append({
                "run_id": run_dir.name,
                "scenario": _safe_text(manifest.get("scenario"), 40),
                "title": _safe_text((_read_json(run_dir / "01_whitepaper.json", {}) or {}).get("title"), 100),
                "status": manifest.get("status") or "unknown",
                "quality": quality_snapshot(run_dir),
                "created": manifest.get("created"),
                "completed_stages": sum(1 for value in stages.values() if (value or {}).get("done")),
                "llm_calls": _prompt_summary(run_dir)["count"],
                "has_06": (run_dir / "06_grounded_questions.json").exists(),
            })
    preferred = {
        "game_showcase__20260906-053636": 0,
        "office__20260717-064826": 1,
        "game__20260625-112210": 2,
        "agent__20260624-214306": 3,
        "cs__20260625-134047": 4,
        "companion__20260624-234524": 5,
        "assistant__20260625-143946": 6,
        "kb__20260625-032549": 7,
    }
    rows.sort(key=lambda row: (preferred.get(row["run_id"], 99), row["run_id"]))
    return {"runs": rows}


@app.get("/api/replay/{run_id}")
def replay_run(run_id: str) -> dict:
    """返回已有 Run 的最终快照和一条 60 秒压缩时间线。"""
    run_id = _normalize_run_id(run_id)
    run_dir = RUNS_DIR / run_id
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="Run 不存在")
    manifest = _read_json(run_dir / "manifest.json", {}) or {}
    if not manifest:
        raise HTTPException(status_code=409, detail="Run 尚无可回放记录")
    events, original_duration = _replay_events(run_dir, manifest)
    if not events:
        raise HTTPException(status_code=409, detail="Run 没有可回放事件")
    final_snapshot = _run_snapshot(run_id, recorded=True)
    final_snapshot["source"] = "replay"
    return {
        "run_id": run_id,
        "duration_ms": 60_000,
        "original_duration_s": round(original_duration, 1),
        "events": events,
        "final_snapshot": final_snapshot,
    }


@app.post("/api/runs", status_code=202)
def create_run(request: RunRequest) -> dict:
    """创建一个真实 Run；MVP 同时只允许一个 Live 任务。"""
    live_ready, _ = _live_configuration()
    if not live_ready:
        raise HTTPException(status_code=503, detail="真实 Live 尚未配置；历史 Replay 仍可使用")
    active = _active_job()
    if active:
        raise HTTPException(status_code=409, detail={"message": "已有 Live Run 正在运行", "run_id": active[0]})

    run_id = f"live__{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    request_path = run_dir / ".live_request.json"
    _atomic_json(request_path, request.model_dump())
    worker_log = (run_dir / "worker.log").open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            [sys.executable, "-u", "-m", "tools.live_demo_worker", "--run-id", run_id, "--request", str(request_path)],
            cwd=ROOT,
            stdout=worker_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        worker_log.close()
    with JOB_LOCK:
        JOBS[run_id] = Job(process=process, created_at=time.time())
    return {"run_id": run_id, "status": "starting", "snapshot_url": f"/api/runs/{run_id}"}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    """读取固定 run_id 的脱敏快照，绝不跟随“最新 Run”。"""
    return _run_snapshot(run_id)


@app.post("/api/runs/{run_id}/cancel", status_code=202)
async def cancel_run(run_id: str) -> dict:
    """取消真实 worker，而不只是在前端改变标签。"""
    run_id = _normalize_run_id(run_id)
    with JOB_LOCK:
        job = JOBS.get(run_id)
    if not job:
        raise HTTPException(status_code=404, detail="找不到可取消的 Live 任务")
    await asyncio.to_thread(_terminate_job, run_id, job)
    return {"run_id": run_id, "status": "cancelled"}


@app.get("/api/recorded")
def recorded_run() -> dict:
    """返回标记为 RECORDED 的历史产物；是否合格由独立 quality 字段表达。"""
    preferred = "game_showcase__20260906-053636"
    if (RUNS_DIR / preferred).is_dir():
        return _run_snapshot(preferred, recorded=True)
    for run_dir in sorted(RUNS_DIR.iterdir(), reverse=True) if RUNS_DIR.exists() else []:
        manifest = _read_json(run_dir / "manifest.json", {}) or {}
        if manifest.get("status") == "done" and (run_dir / "06_grounded_questions.json").exists():
            return _run_snapshot(run_dir.name, recorded=True)
    raise HTTPException(status_code=404, detail="没有可用的历史 Run")
