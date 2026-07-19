"""
eval.memory_systems — 可插拔记忆系统工厂。

make_system("A") / make_system("simpleMem") → SimpleMem 实例
make_system("B") / make_system("fullcontext") → FullContext 实例
make_system("C") / make_system("iterative") → Iterative 实例
外部系统(mem0/zep/memos/amem)注册后同理。
"""
from eval.memory_systems.base import MemorySystem

_ALIASES = {
    "A": "simplemem",
    "B": "fullcontext",
    "C": "iterative",
}

_REGISTRY = {}

# 统一懒加载：不装外部包、未配置 API key 时，import 工厂本身也不应失败。
_LAZY = {
    "simplemem": ("eval.memory_systems.simplemem", "SimpleMem"),
    "fullcontext": ("eval.memory_systems.fullcontext", "FullContext"),
    "iterative": ("eval.memory_systems.iterative", "Iterative"),
    "mem0": ("eval.memory_systems.mem0_adapter", "Mem0Adapter"),
    "zep": ("eval.memory_systems.zep_adapter", "ZepAdapter"),
    "memos": ("eval.memory_systems.memos_adapter", "MemOSAdapter"),
    "amem": ("eval.memory_systems.amem_adapter", "AMemAdapter"),
    "hipporag": ("eval.memory_systems.hipporag_adapter", "HippoRAGAdapter"),
    "letta": ("eval.memory_systems.letta_adapter", "LettaAdapter"),
    "mirix": ("eval.memory_systems.mirix_adapter", "MIRIXAdapter"),
    "raptor": ("eval.memory_systems.raptor_adapter", "RaptorAdapter"),
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
