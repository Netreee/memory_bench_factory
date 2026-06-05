"""
pipeline.stage_c_corpus — Stage C: 准备 corpus + chain 结构。

W1 阶段简化策略:
  OfficeMem 已经有完整的 chain 结构(在 benchmarks/onpolicy_benchmark.json,A→B→C 出),
  我们【复用其 chain + period + ingest_docs 引用】,但【丢弃其 qa】(留给我们 Stage D
  重新签发,而且要带毕设的算子 + 失败模式标签,这是它没有的)。

  这样 W1 可以快速验证 Stage D + E 主线,W3 拿来跟 OfficeMem 原版 qa 做 Spearman 对照。

W4+ 扩第 2 个场景时,Stage C 要做的"通用"版本:
  - 从 corpus_samples (3-5 篇) → 推断 chain 切分规则(时序聚类、cluster ID、editor 链)
  - 把 1899 篇 / 公开数据集中"同一 chain 的文档"分组、按时序排序
  - 输出 list[Chain] 给 Stage D

跑法:
  cd memory_bench_factory
  ./venv/bin/python -m pipeline.stage_c_corpus
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional

from .schema import Chain, Period


OFFICEMEM_BENCHMARK_PATH = Path(
    "/Users/ryanleory/proj/officemem/benchmarks/onpolicy_benchmark.json"
)


def load_chains_from_officemem(
    benchmark_path: Path = OFFICEMEM_BENCHMARK_PATH,
    max_chains: Optional[int] = None,
) -> list[Chain]:
    """从 OfficeMem onpolicy_benchmark.json 复用 chain + period 结构,丢弃 qa。

    返回的每个 Chain 有完整的 (chain_id, chain_name, cluster, periods),
    但 qas=[] —— 留给 Stage D 重新签发带标签的题。
    """
    if not benchmark_path.exists():
        raise FileNotFoundError(f"OfficeMem benchmark 不存在: {benchmark_path}")

    with open(benchmark_path, encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, list):
        raise ValueError(f"onpolicy_benchmark.json 顶层应为 list,实际: {type(raw)}")

    chains: list[Chain] = []
    for i, c in enumerate(raw):
        if max_chains is not None and i >= max_chains:
            break
        periods = []
        for p in c.get("periods", []):
            periods.append(Period(
                period_idx=p["period_idx"],
                # OfficeMem 用 'date' 字段,我们的 schema 用 'timestamp',统一为后者
                timestamp=p.get("date") or p.get("timestamp", ""),
                ingest_docs=list(p.get("ingest_docs", [])),
                state={},  # 留给 Stage D 推断 ground truth 字段值
            ))
        chains.append(Chain(
            chain_id=c["chain_id"],
            chain_name=c.get("chain_name", c["chain_id"]),
            cluster=c.get("cluster"),
            period_count=c.get("period_count", len(periods)),
            periods=periods,
            qas=[],  # ← 这是关键:OfficeMem 的旧 qa 不要,留给 Stage D 重新签发
        ))
    return chains


def summarize_chains(chains: list[Chain]) -> dict:
    """简易统计:总 chain 数 / 总 period 数 / 总 ingest_doc 数 / period_count 分布。"""
    total_periods = sum(c.period_count for c in chains)
    total_docs = sum(len(p.ingest_docs) for c in chains for p in c.periods)
    pc_dist = {}
    for c in chains:
        pc_dist[c.period_count] = pc_dist.get(c.period_count, 0) + 1
    return {
        "n_chains": len(chains),
        "total_periods": total_periods,
        "total_ingest_docs": total_docs,
        "period_count_dist": dict(sorted(pc_dist.items())),
    }


def main():
    """W1 Day-3 自检:加载 OfficeMem chains。"""
    chains = load_chains_from_officemem(max_chains=5)
    print(f"[stage_c] 加载了 {len(chains)} 条 chain (max=5 限制)")
    print()
    stats_5 = summarize_chains(chains)
    print(f"[stage_c] 5 chain 统计: {stats_5}")
    print()
    for c in chains:
        print(f"  chain: {c.chain_id} | name='{c.chain_name}' | cluster={c.cluster}")
        print(f"    period_count={c.period_count}, qas={len(c.qas)} (应为 0,留给 Stage D)")
        for p in c.periods[:3]:
            doc_ids = [d.get("doc_id", "?")[:24] for d in p.ingest_docs]
            print(f"      [period {p.period_idx}] {p.timestamp} | docs={doc_ids}")
        if len(c.periods) > 3:
            print(f"      ... (共 {len(c.periods)} 个 period)")
        print()

    # 全量加载看 corpus 规模
    print("=" * 60)
    chains_all = load_chains_from_officemem()
    stats_all = summarize_chains(chains_all)
    print(f"[stage_c] 全量加载: {stats_all}")


if __name__ == "__main__":
    main()
