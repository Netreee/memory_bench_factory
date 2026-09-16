"""为本地 Live Demo 启动一条真实、隔离的 Memory Forge 流水线。"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import factory
from pipeline.run import Run, drive


def _safe_error(exc: BaseException) -> dict:
    """把异常压成可公开给本地前端的短摘要，不泄漏端点或密钥。"""
    message = str(exc).replace("\n", " ")[:500]
    message = re.sub(r"https?://\S+", "[endpoint]", message)
    message = re.sub(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]?\s*\S+", r"\1=[redacted]", message)
    message = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[redacted]", message)
    return {"type": type(exc).__name__, "message": message or "流水线异常退出", "ts": time.time()}


def main() -> None:
    """读取一次性请求文件，动态注册场景，并真实执行到 quality（07）。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--request", required=True)
    args = parser.parse_args()

    request_path = Path(args.request).resolve()
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    scenario = {
        "description": payload["scenario_text"],
        "few_shot": [{
            "title": Path(payload["sample_name"]).name,
            "content": payload["sample_text"],
            "doc_type": "示例文本",
            "date": time.strftime("%Y-%m-%d"),
        }],
    }

    # 动态场景只存在于这个 worker，完全不污染 factory 的内置场景表。
    factory.SCENARIOS["live"] = scenario
    target_tokens = max(3_000, int(os.getenv("MEMORY_FORGE_DEMO_TARGET_TOKENS", "5000")))
    run = Run(
        "live",
        args.run_id,
        tag="local-live-demo",
        config_meta={
            "from": None,
            "to": "quality",
            "only": None,
            "target_tokens": target_tokens,
            "question_budget": max(1, int(os.getenv("MEMORY_FORGE_DEMO_QUESTION_BUDGET", "30"))),
            "preset": "live-demo",
        },
    )
    run.log(f"=== LIVE DEMO {args.run_id} / target={target_tokens} / stop=07 ===")

    try:
        drive(run, factory.STAGES, to_stage="quality")
    except BaseException as exc:
        error_path = run.dir / ".live_error.json"
        error_path.write_text(json.dumps(_safe_error(exc), ensure_ascii=False), encoding="utf-8")
        raise
    finally:
        # 输入已经进入 00_input.json，临时请求不再需要保留。
        try:
            request_path.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    main()
