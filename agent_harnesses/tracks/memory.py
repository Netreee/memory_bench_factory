"""Memory System Track adapter 占位。

工厂仓库已有若干 MemorySystem 实现，但 context budget、retrieval witness、固定回答模型
和 reset conformance 尚未冻结，因此这里不能把文件存在误报成正式可执行。
"""
from __future__ import annotations

from ..config import ConfigurationError, ExperimentConfig, SystemConfig
from ..planning import RunPlan
from .base import TrackAdapter


class MemoryTrackAdapter(TrackAdapter):
    def preflight(
        self,
        system: SystemConfig,
        experiment: ExperimentConfig,
        plan: RunPlan,
    ) -> dict:
        return {
            "system_id": system.system_id,
            "ok": False,
            "errors": [f"{system.system_id}: Memory Track adapter 尚未接入"],
        }

    def build_command(
        self,
        experiment: ExperimentConfig,
        system: SystemConfig,
        plan: RunPlan,
    ) -> list[str]:
        raise ConfigurationError(
            f"{system.system_id}: Memory Track adapter 尚未接入；"
            "需先冻结 answering model、context budget 与 retrieval witness"
        )
