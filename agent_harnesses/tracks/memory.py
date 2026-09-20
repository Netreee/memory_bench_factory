"""Memory System Track adapter。

把统一的 RunPlan 翻成 memory runner 的执行动作。与 native 赛道共用 harness 的
配置校验、run plan、产物与判分；只有「准备 + 作答」这层由 `runners/memory.py` 负责。

冻结口径（D-20260920-02，2026-09-20 由用户确认）：

- 固定回答模型：experiment 顶层 `answering_model`（本 run 的唯一模型，抽桥同用）；
- 检索：`simplemem` / `iterative` 的 `retrieval.top_k`；`fullcontext` 的
  `memory_system.full_context_char_budget`；
- `context_token_budget` 暂不强制（记实测值），`witness_required` 暂不强制为门；
- 一个世界：ingest 一次且串行；并行只作用于逐题作答。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from ..config import REPOSITORY_ROOT, ConfigurationError, ExperimentConfig, SystemConfig
from ..planning import RunPlan
from .base import TrackAdapter

# 需要本地 bge embedding 的内置实现（fullcontext 不需要）。
EMBEDDING_SYSTEMS = {"simplemem", "iterative"}
EMBEDDING_REQUIREMENTS = ("numpy", "sentence_transformers")


def _memory_system_id(system: SystemConfig) -> str:
    spec = system.spec if isinstance(system.spec, dict) else {}
    memory = spec.get("memory_system") or {}
    name = str(memory.get("id") or "").strip()
    if not name:
        raise ConfigurationError(f"{system.system_id}: spec.memory_system.id 不能为空")
    return name


class MemoryTrackAdapter(TrackAdapter):
    def preflight(
        self,
        system: SystemConfig,
        experiment: ExperimentConfig,
        plan: RunPlan,
    ) -> dict:
        errors: list[str] = []
        warnings: list[str] = []

        try:
            name = _memory_system_id(system)
        except ConfigurationError as exc:
            name, errors = "", [str(exc)]

        # 1) 固定回答模型：experiment 顶层必须声明，且 system 不得带 target 级模型。
        answering = experiment.answering_model or {}
        if not answering:
            errors.append(
                "Memory Track 必须在 experiment 顶层固定 answering_model（model_id + endpoint_profile）"
            )
        if system.implementation.get("runner") != "memory":
            errors.append(
                f"{system.system_id}: implementation.runner 必须是 memory，"
                f"当前 {system.implementation.get('runner')!r}"
            )

        # 2) benchmark 与 endpoint 密钥。
        benchmark = Path(plan.benchmark.get("path", ""))
        if not benchmark.is_dir():
            errors.append(f"benchmark 目录不存在: {benchmark}")
        endpoint = {}
        if answering:
            profile = str(answering.get("endpoint_profile") or "")
            from ..runners.native_cli import _profile_names, load_env_file

            key_name, base_name = _profile_names(profile)
            env_file = REPOSITORY_ROOT / "configs" / "env" / "secrets.env"
            if not env_file.is_file():
                errors.append(f"缺失本地配置: {env_file}（从 secrets.env.example 复制）")
            else:
                env = load_env_file(env_file)
                for field, value in ((key_name, env.get(key_name)), (base_name, env.get(base_name))):
                    if not value:
                        errors.append(f"answering_model.endpoint_profile={profile} 缺少 {field}")
                endpoint = {
                    "endpoint_profile": profile,
                    "endpoint_host": str(env.get(base_name) or "").split("//", 1)[-1].split("/", 1)[0],
                }

        # 3) 内置实现的运行期依赖（fail-closed：缺依赖时不要跑到一半才炸）。
        missing = [
            module for module in (EMBEDDING_REQUIREMENTS if name in EMBEDDING_SYSTEMS else ())
            if importlib.util.find_spec(module) is None
        ]
        if missing:
            errors.append(
                f"{system.system_id}({name}) 需要本地 bge embedding 依赖: "
                f"{', '.join(missing)} 未安装（pip install numpy sentence-transformers）"
            )
        elif name in EMBEDDING_SYSTEMS:
            if importlib.util.find_spec("transformers") is None:
                warnings.append("transformers 未安装；sentence-transformers 首次加载会自行解析")

        spec = system.spec if isinstance(system.spec, dict) else {}
        retrieval = spec.get("retrieval") or {}
        return {
            "system_id": system.system_id,
            "adapter": "memory",
            "ok": not errors,
            "memory_system": name,
            "track": "memory",
            "benchmark": str(benchmark),
            "answering_model": dict(answering),
            **endpoint,
            "retrieval": dict(retrieval),
            "python": sys.executable,
            "warnings": warnings,
            "errors": errors,
        }

    def build_command(
        self,
        experiment: ExperimentConfig,
        system: SystemConfig,
        plan: RunPlan,
    ) -> list[str]:
        if not plan.executable:
            raise ConfigurationError(f"{system.system_id}: system 未标记为可执行")
        limit = int(plan.execution.get("limit") or 0)
        return [
            sys.executable,
            "-m",
            "agent_harnesses.runners.memory",
            "--plan",
            str(Path(plan.output_dir) / "run_plan.json"),
            "--run",
            str(plan.benchmark.get("path")),
            "--out",
            str(plan.output_dir),
            "--limit",
            str(limit),
        ]
