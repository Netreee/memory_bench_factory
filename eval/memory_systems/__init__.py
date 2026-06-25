"""
eval.memory_systems — 可插拔记忆系统工厂。

make_system("A") / make_system("simpleMem") → SimpleMem 实例
make_system("B") / make_system("fullcontext") → FullContext 实例
make_system("C") / make_system("iterative") → Iterative 实例
外部系统(mem0/zep/memos/amem)注册后同理。
"""
from eval.memory_systems.base import MemorySystem
from eval.memory_systems.simplemem import SimpleMem
from eval.memory_systems.fullcontext import FullContext
from eval.memory_systems.iterative import Iterative

_ALIASES = {
    "A": "simplemem",
    "B": "fullcontext",
    "C": "iterative",
}

_REGISTRY = {
    "simplemem": SimpleMem,
    "fullcontext": FullContext,
    "iterative": Iterative,
}

# 外部系统懒加载(不装 pip 包也不影响内置 baseline)
_LAZY = {
    "mem0": ("eval.memory_systems.mem0_adapter", "Mem0Adapter"),
    "zep": ("eval.memory_systems.zep_adapter", "ZepAdapter"),
    "memos": ("eval.memory_systems.memos_adapter", "MemOSAdapter"),
    "amem": ("eval.memory_systems.amem_adapter", "AMemAdapter"),
}


def make_system(name: str, **kwargs) -> MemorySystem:
    """按名称创建记忆系统实例。
    name: "A"/"B"/"C" 或 "simplemem"/"fullcontext"/"iterative"/"mem0"/"zep"/"memos"/"amem"。
    kwargs: 透传给构造函数(如 top_k, embed_mem, budget)。"""
    key = _ALIASES.get(name.upper(), name.lower())
    cls = _REGISTRY.get(key)
    if cls is None and key in _LAZY:
        mod_path, cls_name = _LAZY[key]
        import importlib
        mod = importlib.import_module(mod_path)
        cls = getattr(mod, cls_name)
        _REGISTRY[key] = cls
    if cls is None:
        raise ValueError(f"未知记忆系统: {name!r}  (可用: {sorted(set(list(_REGISTRY.keys()) + list(_LAZY.keys())))})")
    return cls(**kwargs)


def register(name: str, cls: type) -> None:
    """注册外部系统 adapter。"""
    _REGISTRY[name.lower()] = cls
