"""
pipeline.scenario_loader — 从 OfficeMem 1899 篇真实文档加载 ScenarioSpec。

W1 主要测试输入:从 OfficeMem 中采样 5 篇代表性文档,组装成 ScenarioSpec_office,
作为 pipeline 的 few-shot 输入。后续 W3 关键实验:
  - benchmark_v3 (用我们 pipeline + 5 篇 corpus 生成)
  - vs OfficeMem 原版 benchmark (用其 ABCD pipeline + 1899 篇全量生成)
  - 看 9 baseline × 3 R 范式 = 27 cells 上的 Spearman 相关

参考:
  docs/anchors/scenario_spec.md  — Pipeline 输入设计原则
  /Users/ryanleory/proj/officemem/  — 真实办公场景 corpus

跑法(W1 Day-1 调通用):
  cd memory_bench_factory
  ./venv/bin/python -m pipeline.scenario_loader
"""
from __future__ import annotations
import json
import os
import random
from pathlib import Path
from typing import Optional

from .schema import Document, ScenarioSpec


OFFICEMEM_ROOT = Path("/Users/ryanleory/proj/officemem")
HUMAN_MD_DIR = OFFICEMEM_ROOT / "human_readable_md"


def load_document(doc_dir: Path) -> Optional[Document]:
    """从 OfficeMem 单个 doc 目录加载 Document(.md + metadata.json)。

    OfficeMem 目录约定:
      human_readable_md/{DOC_ID}/
        ├── {DOC_ID}.md
        ├── metadata.json
        └── img_*.png
    """
    metadata_path = doc_dir / "metadata.json"
    if not metadata_path.exists():
        return None
    try:
        with open(metadata_path, encoding="utf-8") as f:
            metadata = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    doc_id = metadata.get("id") or doc_dir.name
    md_path = doc_dir / f"{doc_id}.md"
    if not md_path.exists():
        return None
    try:
        with open(md_path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None

    # 把 OfficeMem 的 metadata 字段映射到我们的 Document.metadata
    mapped = {
        "timestamp": metadata.get("last_modified_str") or metadata.get("start_time_str"),
        "doc_type": metadata.get("doc_type_pred"),
        "author": (metadata.get("editors") or [None])[0],
        "ai_summary": metadata.get("ai_summary"),
        "ai_tags": metadata.get("ai_tags"),
        "ai_entities": metadata.get("ai_entities"),
    }
    return Document(
        doc_id=doc_id,
        title=metadata.get("title", ""),
        content=content,
        metadata=mapped,
    )


def sample_office_corpus(
    n: int = 5,
    seed: int = 42,
    doc_type_filter: Optional[list] = None,
) -> list[Document]:
    """从 OfficeMem 中随机采样 n 篇文档。

    Args:
        n: 采样数量(默认 5,符合 ScenarioSpec 设计原则)
        seed: 随机种子,保证可复现
        doc_type_filter: 可选,只采样含这些 doc_type 关键词的文档
    """
    if not HUMAN_MD_DIR.exists():
        raise FileNotFoundError(f"OfficeMem corpus 不存在: {HUMAN_MD_DIR}")

    all_doc_dirs = sorted([d for d in HUMAN_MD_DIR.iterdir() if d.is_dir()])
    if not all_doc_dirs:
        raise ValueError(f"OfficeMem 目录为空: {HUMAN_MD_DIR}")

    rng = random.Random(seed)
    rng.shuffle(all_doc_dirs)

    docs: list[Document] = []
    examined = 0
    for d in all_doc_dirs:
        if len(docs) >= n:
            break
        examined += 1
        doc = load_document(d)
        if doc is None:
            continue
        if doc_type_filter:
            dt = (doc.metadata or {}).get("doc_type") or ""
            if not any(k in dt for k in doc_type_filter):
                continue
        docs.append(doc)

    if len(docs) < n:
        print(f"[scenario_loader] warn: 只采样到 {len(docs)} / {n} 篇符合条件的文档"
              f"(共扫描 {examined} 个目录)")
    return docs


def build_scenario_office(
    n_corpus: int = 5,
    seed: int = 42,
    doc_type_filter: Optional[list] = None,
) -> ScenarioSpec:
    """组装 W1 主要测试输入:ScenarioSpec_office。

    description 沿用 docs/anchors/scenario_spec.md 第 3 节的范例,
    站在【业务线 leader 视角】关注项目状态演化与跨文档一致性。
    """
    description = (
        "企业内部办公文档系列(主要是周报、技术设计文档、会议纪要、内部说明文档),"
        "跨度数周到数月。文档由不同的项目负责人/工程师/产品撰写,反映项目状态、"
        "技术方案演进、跨期字段变化。同一项目/业务线下,字段值(缺陷率、容量、"
        "进度、负责人等)会随时间变化,新文档可能与旧文档冲突。\n"
        "评测应站在【业务线 leader 视角】,关注:\n"
        "  - 当前项目/业务线的真实状态是什么\n"
        "  - 哪些字段在什么事件触发下变过\n"
        "  - 同一项目内多个文档之间是否一致\n"
        "  - 历史结论是否仍然适用(需识别过期)"
    )
    corpus = sample_office_corpus(n=n_corpus, seed=seed, doc_type_filter=doc_type_filter)
    return ScenarioSpec(
        name="office_engineering",
        description=description,
        corpus_samples=corpus,
        temporal_hint="project_phase",
        subject_hint="project",
        perspective_hint="cross_line_leader",
        target_size=30,
    )


def build_scenario_office_no_hint(
    n_corpus: int = 5,
    seed: int = 42,
) -> ScenarioSpec:
    """无 hint 版本:用于测试 Stage A 启发式推断能否独立工作。

    description 中保留"业务线 leader 视角"等关键词,让启发式可从 description 信号 pick up;
    但故意【不传】temporal_hint / subject_hint / perspective_hint 三个显式参数。
    """
    description = (
        "企业内部办公文档系列(主要是周报、技术设计文档、会议纪要、内部说明文档),"
        "跨度数周到数月。文档由不同的项目负责人/工程师/产品撰写,反映项目状态、"
        "技术方案演进、跨期字段变化。同一项目/业务线下,字段值(缺陷率、容量、"
        "进度、负责人等)会随时间变化,新文档可能与旧文档冲突。\n"
        "评测应站在【业务线 leader 视角】,关注:\n"
        "  - 当前项目/业务线的真实状态是什么\n"
        "  - 哪些字段在什么事件触发下变过\n"
        "  - 同一项目内多个文档之间是否一致\n"
        "  - 历史结论是否仍然适用(需识别过期)"
    )
    corpus = sample_office_corpus(n=n_corpus, seed=seed)
    return ScenarioSpec(
        name="office_engineering_no_hint",
        description=description,
        corpus_samples=corpus,
        # 故意不传 3 个 hint,测启发式独立工作能力
        target_size=30,
    )


def _print_spec(spec: ScenarioSpec) -> None:
    print(f"ScenarioSpec: {spec.name}")
    print(f"  description (前 80 字): {spec.description[:80]}...")
    print(f"  hints: temporal={spec.temporal_hint}, subject={spec.subject_hint}, "
          f"perspective={spec.perspective_hint}")
    print(f"  corpus_samples: {len(spec.corpus_samples)} 篇")
    for d in spec.corpus_samples:
        dt = (d.metadata or {}).get("doc_type") or "?"
        ts = (d.metadata or {}).get("timestamp") or "?"
        print(f"    - [{dt}] {d.title[:40]}")
        print(f"      doc_id={d.doc_id}  timestamp={ts}  len={len(d.content)} chars")


def main():
    print("=" * 60)
    print("【带 hint】build_scenario_office()")
    print("=" * 60)
    _print_spec(build_scenario_office())
    print()
    print("=" * 60)
    print("【无 hint】build_scenario_office_no_hint()")
    print("=" * 60)
    _print_spec(build_scenario_office_no_hint())


if __name__ == "__main__":
    main()
