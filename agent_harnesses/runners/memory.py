"""Memory System Track runner。

一个世界：把 `05_corpus.json` 按 session 顺序 ingest 进被测记忆系统，再对全部题目
逐题 `retrieve()` → 统一答题 prompt 合成答案，逐题落 `results.jsonl`。

职责边界（与 native 赛道一致）：

- **harness 拥有**：run 目录、`results.jsonl`/`summary.json` 形状、并发调度、错误分型、
  答题模型的 endpoint 绑定，以及后续由 `agent_harnesses score` 完成的判分。
- **factory 拥有（原样复用，不改语义）**：`eval.memory_systems` 的 A/B/C 实现、
  `eval.baseline_r1.unified_answer` 的答题 prompt 与 `answer_protocol` 注入、
  `eval/judge.py` 的判分口径。

并发：ingest 是一次性的串行阶段（一个世界）；并行只加在**逐题作答**上，因此
`retrieve()` 在 `finalize_ingest()` 之后必须是只读的。并发度不进 fingerprint。

用法（由 `tracks/memory.py` 生成 argv，不手工调用）:

    python -m agent_harnesses.runners.memory \
        --plan <run_dir>/run_plan.json --run <benchmark_dir> --out <run_dir> --limit N
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..config import REPOSITORY_ROOT, ConfigurationError
from .native_cli import _profile_names, load_env_file

RESULT_SCHEMA = "agent-harnesses.result/v1"
SUMMARY_SCHEMA = "agent-harnesses.memory-summary/v1"
QA_MAX_TOKENS = 2048
DEFAULT_TOP_K = 3

# 与 native 赛道对齐：native 专属字段在 memory run 里保持存在但置空/置 False，
# 这样 score / aggregate / 未来的跨赛道报表可以用同一套读取代码。
NATIVE_ONLY_FIELDS = {
    "cli_report": None,
    "raw_stdout": None,
    "raw_stderr": None,
    "events": None,
    "telemetry": None,
    "boundary_violation": False,
    "boundary_violation_details": None,
    "contaminated": False,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _question_id(question: Mapping[str, Any]) -> str:
    return str(
        question.get("question_id")
        or hashlib.sha256(question["question"].encode("utf-8")).hexdigest()[:16]
    )


def _load_json(path: Path) -> Any:
    if not path.is_file():
        raise ConfigurationError(f"benchmark 缺少文件: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _load_plan(plan_path: Path) -> dict[str, Any]:
    path = Path(plan_path)
    if not path.is_file():
        raise ConfigurationError(f"缺 run plan: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _sessions_from_docs(docs: list) -> list[dict]:
    """把 doc 粒度的 [(sid, date, content), ...] 收敛成 adapter 要的 session 粒度。"""
    grouped: dict[int, dict] = {}
    for sid, date, content in docs:
        entry = grouped.setdefault(
            int(sid), {"session_id": int(sid), "date": date, "docs": []}
        )
        entry["docs"].append(content)
    return [grouped[sid] for sid in sorted(grouped)]


def _system_kwargs(spec: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """把 systems.toml 的 spec 翻成 `make_system` 的 name + kwargs。"""
    memory = spec.get("memory_system") or {}
    name = str(memory.get("id") or "").strip()
    if not name:
        raise ConfigurationError("system.spec.memory_system.id 不能为空")
    retrieval = spec.get("retrieval") or {}
    top_k = int(retrieval.get("top_k") or DEFAULT_TOP_K)
    kwargs: dict[str, Any] = {}
    if name == "simplemem":
        kwargs["top_k"] = top_k
    elif name == "iterative":
        kwargs["top_k"] = top_k
        # 抽桥那次调用的 token 预算。默认 2048 = 合作者的 legacy 默认值；对推理模型
        # 会因 reasoning 吃掉预算而 output_truncated，需要时由 systems.toml 调高。
        kwargs["max_tokens"] = int(retrieval.get("hop_max_tokens") or 2048)
    elif name == "fullcontext":
        kwargs["budget"] = int(memory.get("full_context_char_budget") or 120_000)
    return name, kwargs


def _bind_answering_model(
    plan: Mapping[str, Any], env: Mapping[str, str]
) -> dict[str, Any]:
    """把 experiment 固定的 answering_model 绑定到 factory 的进程级 LLM 入口。

    `unified_answer` 与 `iterative` 的抽桥都读 factory `config` 的模块级
    MODEL/API_KEY/BASE_URL（由 `load_dotenv` 在 import 时填充，且不覆盖已有环境变量），
    因此在 import factory 之前先写 os.environ，就能让 answering_model 成为本 run 的
    唯一模型，无需修改合作者的代码。
    """
    answering = plan.get("answering_model") or {}
    model_id = str(answering.get("model_id") or "").strip()
    profile = str(answering.get("endpoint_profile") or "").strip()
    if not model_id or not profile:
        raise ConfigurationError(
            "Memory Track 需要 experiment 顶层 answering_model 的 model_id 与 endpoint_profile"
        )
    key_name, base_name = _profile_names(profile)
    api_key = str(env.get(key_name) or "").strip()
    base_url = str(env.get(base_name) or "").strip()
    if not api_key or not base_url:
        raise ConfigurationError(
            f"answering_model.endpoint_profile={profile} 需要 {key_name} 与 {base_name}"
        )
    os.environ["OPENAI_API_KEY"] = api_key
    os.environ["OPENAI_BASE_URL"] = base_url
    os.environ["MODEL"] = model_id
    return {
        "label": str(answering.get("label") or model_id),
        "model_id": model_id,
        "endpoint_profile": profile,
        "endpoint_host": base_url.split("//", 1)[-1].split("/", 1)[0],
    }


def _load_factory() -> dict[str, Any]:
    """import factory 侧被复用的原语（必须在 os.environ 绑定之后）。"""
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    import config as factory_config  # noqa: PLC0415
    from eval.baseline_r1 import unified_answer  # noqa: PLC0415
    from eval.memory_systems import make_system  # noqa: PLC0415
    from eval.memory_systems.base import MemoryExecutionError  # noqa: PLC0415
    from eval.provenance import (  # noqa: PLC0415
        load_public_protocol,
        load_visible_corpus,
        make_evaluation_context,
    )

    return {
        "config": factory_config,
        "unified_answer": unified_answer,
        "make_system": make_system,
        "MemoryExecutionError": MemoryExecutionError,
        "load_public_protocol": load_public_protocol,
        "load_visible_corpus": load_visible_corpus,
        "make_evaluation_context": make_evaluation_context,
    }


def _failure(exc: Exception, stage: str) -> dict[str, Any]:
    """失败证据：优先用 adapter 自带的 stage；裸异常按调用点归因。

    裸异常（例如答题模型的传输错误）不带 stage，如果一律记成 unknown，错误分型就会
    退化成 memory_error，丢失「是检索坏了还是答题坏了」这条信息。

    adapter 的 `execution_stage` 只保留 `cause_type`，而底层异常往往自带可诊断的
    结构化字段（如 `ChatJSONError.kind/attempts/token_cap`）。这里把这些非敏感字段
    取出来，避免排障时只能看到「operation_failed」。不记录任何原始响应文本。
    """
    as_dict = getattr(exc, "as_dict", None)
    detail: dict[str, Any] | None = None
    if callable(as_dict):
        try:
            candidate = as_dict()
            if isinstance(candidate, dict) and candidate.get("stage"):
                detail = candidate
        except Exception:  # pragma: no cover - 防御 adapter 自身抛错
            pass
    if detail is None:
        detail = {"stage": stage, "code": type(exc).__name__, "details": {}}

    cause = exc.__cause__
    if isinstance(cause, BaseException):
        details = dict(detail.get("details") or {})
        details.setdefault("cause_type", type(cause).__name__)
        for field in ("kind", "attempts", "token_cap", "phase", "usage_status",
                      "response_received", "input_chars"):
            value = getattr(cause, field, None)
            if isinstance(value, (str, int, float, bool)):
                details.setdefault(f"cause_{field}", value)
        detail["details"] = details
    return detail


def _error_type(stage: str) -> str:
    return {
        "ingest": "memory_ingest_error",
        "finalize": "memory_ingest_error",
        "retrieve": "memory_retrieve_error",
        "answer": "memory_answer_error",
    }.get(stage, "memory_error")


def _retrieval_metadata(instance: Any, context: str, kwargs: Mapping[str, Any]) -> dict:
    """记录可复核的检索事实（不改检索本身）。"""
    meta: dict[str, Any] = {
        "context_chars": len(context),
        "top_k": kwargs.get("top_k"),
        "empty": context.strip() == "(无检索结果)",
    }
    get_diagnostics = getattr(instance, "get_diagnostics", None)
    if callable(get_diagnostics):
        try:
            diagnostics = get_diagnostics() or {}
            if isinstance(diagnostics, dict) and diagnostics:
                meta["diagnostics"] = diagnostics
        except Exception:  # pragma: no cover - 诊断失败不能影响作答
            pass
    trunc = getattr(instance, "trunc_info", None)
    if isinstance(trunc, dict):
        meta["full_context"] = trunc
    return meta


def _base_record(index: int, question: Mapping[str, Any], system_id: str) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "system_id": system_id,
        "question_id": _question_id(question),
        "question_index": index,
        "status": "completed",
        "answer": None,
        "judgeable": False,
        "correct": None,
        "error_type": None,
        "error_detail": None,
        "returncode": None,
        "elapsed_s": None,
        "usage": None,
        "usage_note": "unified_answer 不返回 usage；memory track 的 token 记账待补",
    }
    record.update(NATIVE_ONLY_FIELDS)
    return record


def resolve_env_file(plan: Mapping[str, Any]) -> Path:
    """memory runner 与 native 共用一份 secrets.env；system 可显式覆盖。"""
    raw = str(
        ((plan.get("system") or {}).get("implementation") or {}).get("env_file") or ""
    ).strip()
    if not raw:
        return REPOSITORY_ROOT / "configs" / "env" / "secrets.env"
    path = Path(raw)
    return path if path.is_absolute() else (REPOSITORY_ROOT / path).resolve()


def _pin_huggingface_cache() -> None:
    """把 HuggingFace 缓存钉在工作区内，保证 bge 权重落盘位置可预期。

    受限环境（沙箱 / 只读 HOME）下默认的 `~/.cache/huggingface` 不可写，会在
    `finalize_ingest` 阶段才炸；这里提前选定一个仓内、可随 output/ 一起清理的位置。
    需要复用已有全局缓存时，显式设置 `HF_HOME` 即可覆盖。
    """
    if os.environ.get("HF_HOME"):
        return
    os.environ["HF_HOME"] = str(REPOSITORY_ROOT / "output" / "eval" / "_hf_cache")


def _write_rows(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def run(
    plan_path: Path,
    out_dir: Path,
    limit: int,
    questions_in_parallel: int | None = None,
) -> dict[str, Any]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    started_at = _now_iso()
    plan = _load_plan(Path(plan_path))
    runner = plan.get("runner")
    if runner != "memory":
        raise ConfigurationError(f"memory runner 不接受 runner={runner!r} 的 plan")

    system = plan.get("system") or {}
    system_id = str(plan.get("system_id") or system.get("system_id") or "")
    _pin_huggingface_cache()
    env = load_env_file(resolve_env_file(plan))
    answering = _bind_answering_model(plan, env)
    factory = _load_factory()

    # 绑定后 factory_config 的模块级身份必须与 experiment 一致（load_dotenv 不覆盖
    # 已有环境变量，这里再显式对齐一次，避免 .env 的缺省值意外生效）。
    factory["config"].MODEL = answering["model_id"]
    factory["config"].API_KEY = os.environ["OPENAI_API_KEY"]
    factory["config"].BASE_URL = os.environ["OPENAI_BASE_URL"]

    benchmark_dir = Path(plan["benchmark"]["path"])
    questions = _load_json(benchmark_dir / "06_grounded_questions.json")
    if isinstance(questions, dict):
        questions = questions.get("questions") or []
    docs = factory["load_visible_corpus"](benchmark_dir / "05_corpus.json")
    protocol = factory["load_public_protocol"](benchmark_dir / "00_about.json")
    sessions = _sessions_from_docs(docs)
    evaluation_context = factory["make_evaluation_context"](docs, protocol)

    total = len(questions) if int(limit) <= 0 else min(int(limit), len(questions))
    limited = list(enumerate(questions))[:total]
    name, kwargs = _system_kwargs(system.get("spec") or {})
    execution = plan.get("execution") or {}
    parallel = max(1, int(questions_in_parallel or execution.get("questions_in_parallel") or 1))

    # ── 阶段 1：ingest（串行，一次性）───────────────────────────────────────
    ingest_started = time.monotonic()
    instance = None
    ingest_error: dict[str, Any] | None = None
    receipts: list[dict] = []
    try:
        instance = factory["make_system"](name, **kwargs)
        for session in sessions:
            receipt = instance.ingest_session(session)
            if not isinstance(receipt, dict) or receipt.get("status") != "ok":
                raise factory["MemoryExecutionError"](
                    "ingest", "invalid_receipt",
                    {"session_id": session["session_id"], "receipt": receipt},
                )
            if int(receipt.get("n_docs") or 0) != len(session["docs"]):
                raise factory["MemoryExecutionError"](
                    "ingest", "partial_write",
                    {"session_id": session["session_id"],
                     "expected": len(session["docs"]), "receipt": receipt},
                )
            receipts.append(receipt)
        instance.finalize_ingest()
    except Exception as exc:  # noqa: BLE001 - 任何 ingest 失败都要落成可读证据
        ingest_error = _failure(exc, "ingest")
        instance = None
    ingest_elapsed = round(time.monotonic() - ingest_started, 3)

    # ── 阶段 2：逐题 retrieve + answer（可并行）─────────────────────────────
    write_lock = threading.Lock()
    results_path = out_dir / "results.jsonl"
    rows: list[dict] = []

    def worker(item: tuple[int, Mapping[str, Any]]) -> dict[str, Any]:
        index, question = item
        record = _base_record(index, question, system_id)
        if ingest_error is not None:
            record["status"] = "failed"
            record["error_type"] = _error_type(str(ingest_error.get("stage") or "ingest"))
            record["error_detail"] = ingest_error
            return record
        started = time.monotonic()
        stage = "retrieve"
        try:
            context = instance.retrieve(
                question["question"], top_k=kwargs.get("top_k", DEFAULT_TOP_K)
            )
            if not isinstance(context, str):
                raise factory["MemoryExecutionError"](
                    "retrieve", "invalid_response", {"cause_type": type(context).__name__}
                )
            record["retrieval"] = _retrieval_metadata(instance, context, kwargs)
            stage = "answer"
            record["answer"] = factory["unified_answer"](
                question["question"], context, protocol=protocol, max_tokens=QA_MAX_TOKENS
            )
        except Exception as exc:  # noqa: BLE001
            detail = _failure(exc, stage)
            record["status"] = "failed"
            record["error_type"] = _error_type(str(detail.get("stage") or stage))
            record["error_detail"] = detail
        record["elapsed_s"] = round(time.monotonic() - started, 3)
        return record

    def commit(row: dict[str, Any]) -> None:
        with write_lock:
            rows.append(row)
            _write_rows(results_path, sorted(rows, key=lambda r: r["question_index"]))

    workers = min(parallel, len(limited)) or 1
    if workers <= 1:
        for item in limited:
            commit(worker(item))
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mb-memory") as pool:
            futures = [pool.submit(worker, item) for item in limited]
            for future in as_completed(futures):
                commit(future.result())
    _write_rows(results_path, sorted(rows, key=lambda r: r["question_index"]))

    errors_by_type: dict[str, int] = {}
    for row in rows:
        key = row.get("error_type")
        if key:
            errors_by_type[str(key)] = errors_by_type.get(str(key), 0) + 1
    summary = {
        "schema": SUMMARY_SCHEMA,
        "system_id": system_id,
        "adapter": "memory",
        "memory_system": name,
        "memory_kwargs": kwargs,
        "model": answering,
        "n_sessions": len(sessions),
        "n_docs": len(docs),
        "n_questions": len(limited),
        "n_errors": sum(errors_by_type.values()),
        "n_errors_by_type": errors_by_type,
        "ingest": {
            "elapsed_s": ingest_elapsed,
            "n_sessions": len(receipts),
            "n_docs": sum(int(r.get("n_docs") or 0) for r in receipts),
            "error": ingest_error,
            "note": "一个世界：ingest 一次且串行；并行只作用于逐题作答",
        },
        "concurrency": {
            "questions_in_parallel": workers,
            "workers_used": workers,
            "questions_dispatched": len(limited),
            "note": "并发度只影响调度与落盘顺序，不进 fingerprint",
        },
        "protocol": {"injected": bool(protocol), "chars": len(protocol)},
        "evaluation_context": evaluation_context,
        "scorable": False,
        "contaminated_run": False,
        "secret_leaks": False,
        "telemetry": {
            "status": "unavailable",
            "note": "memory track 没有 CLI 事件流，工具级 telemetry 不适用",
        },
        "boundary": {
            "n_questions_with_violation": 0,
            "violations": [],
            "contaminated_run": False,
            "note": "memory track 不经过 agent 原生工具，无边界检测语义",
        },
        "started_at": started_at,
        "finished_at": _now_iso(),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-harnesses-memory", description="Memory System Track runner"
    )
    parser.add_argument("--plan", required=True, help="run_plan.json")
    parser.add_argument("--run", required=True, help="benchmark 目录（含 00/05/06）")
    parser.add_argument("--out", required=True, help="run 输出目录")
    parser.add_argument("--limit", type=int, default=0, help="最多答几题；0 表示全部")
    parser.add_argument("--questions-in-parallel", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        summary = run(
            Path(args.plan),
            Path(args.out),
            args.limit,
            questions_in_parallel=args.questions_in_parallel,
        )
    except ConfigurationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
