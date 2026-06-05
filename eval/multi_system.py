"""
eval.multi_system — 多系统记忆评测 harness(验证 L1 vs L2 的【排名翻转】区分度)。

ONE world / 单链:ingest 一次语料,问全部题。对每个记忆系统跑同一套题,
按 line(L1_timeline / L2_relational)与 capability 聚合准确率,观察跨系统排名是否翻转。

系统(架构由弱到强,专打不同能力层):
  A = SingleShotRAG   : EmbedMemory(top_k=3) 检索 → r1_answer 单轮合成。最朴素 RAG。
  B = FullContextRAG  : 全部 docs(带 [周期|日期] 表头)塞进一个 prompt 一次性作答。
                        = 无检索瓶颈的上界(对 L1 信号竞争反而引入噪声)。
  C = IterativeRAG    : 先 retrieve → 第一跳 LLM 抽桥实体 → 用桥再 retrieve → 合成。
                        = L2 多跳真正需要的架构(A 取不到被隐藏的桥文档)。

排名翻转假设:L1 上 A ≳ B;L2 多跳上 B/C ≫ A。若观察到 = benchmark 能按能力层级
区分记忆架构 = 区分度终极证据。

跑法:
  ./venv/bin/python -m eval.multi_system                       # 默认 office_v3,A/B/C 全跑
  ./venv/bin/python -m eval.multi_system --smoke               # 烟测(2题×全系统)
  ./venv/bin/python -m eval.multi_system --systems A,B         # 只跑指定系统
  ./venv/bin/python -m eval.multi_system --bench X.json --corpus Y.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config
from eval.memory_interface import EmbedMemory, embed_texts, _chunk
from eval.baseline_r1 import r1_answer, R1_SYSTEM
from eval.judge import judge

# ── 默认评测集(office_v3) ───────────────────────────────────────────────────
DEFAULT_BENCH = ROOT / "output" / "factory_v2_office_v3" / "06_questions.json"
DEFAULT_CORPUS = ROOT / "output" / "factory_v2_office_v3" / "05_corpus.json"
OUT_JSON = ROOT / "output" / "eval" / "multi_system_office_v3.json"
OUT_REPORT = ROOT / "output" / "eval" / "multi_system_report.md"

# 全量语料约 10万字符(~120k token),模型实测能吃下;留个安全闸,超大语料才截断。
FULLCTX_CHAR_BUDGET = 120_000
TOP_K = 3
QA_MAX_TOKENS = 2048
WORKERS = 6

# 排序题:本轮不计入主表(需 Kendall-τ,单列说明)。
SKIP_CAPABILITIES = {"ORDER"}

L1_CAPS_ORDER = ["IE", "KU", "TR", "MR", "FORGET", "PREEXPIRE", "CONFLICT"]


# ─────────────────────────────────────────────────────────────────────────────
# 金串抽取:把各 capability 的代码烘焙 gt 抽成【可判分的金串集 list[str]】。
# 回空 list = 不可判分(本 harness 不打分项) → 该题剔除不计分。
# ─────────────────────────────────────────────────────────────────────────────
def gold_strings(q: dict) -> list:
    cap = q["capability"]
    gt = q.get("gt")

    if cap in ("KU", "PREEXPIRE", "L2_multihop"):
        # 纯串答案(KU 最新值 / PREEXPIRE 停统计前最后值 / L2 多跳终点)
        return [str(gt)] if isinstance(gt, str) and gt.strip() else []

    if cap == "IE":
        v = (gt or {}).get("value")
        return [str(v)] if v else []

    if cap == "MR":
        v = (gt or {}).get("value")
        return [str(v)] if v not in (None, "") else []

    if cap == "TR":
        # 变更题:命中【目标值】或【变更日期】之一即算答对该事件
        out = []
        for k in ("to", "date"):
            v = (gt or {}).get(k)
            if v not in (None, ""):
                out.append(str(v))
        return out

    # FORGET / CONFLICT / ORDER 等结构化判定题:本 harness 用金串-judge 打不了,剔除。
    # (FORGET 的"正确"是模型答'信息不足/已停统计',CONFLICT 是布尔——非金串匹配范式。)
    return []


# ─────────────────────────────────────────────────────────────────────────────
# 语料加载 + 公平 ingest(照搬 run_eval:每片段自带 [周期|日期] 表头)
# ─────────────────────────────────────────────────────────────────────────────
def load_corpus(corpus_path: Path) -> list:
    """返回按 session_id 升序的 [(sid, date, content), ...](doc 粒度)。"""
    data = json.loads(Path(corpus_path).read_text(encoding="utf-8"))
    sessions = sorted(data["corpus"]["sessions"], key=lambda s: int(s["session_id"]))
    docs = []
    for s in sessions:
        for d in s["docs"]:
            docs.append((int(s["session_id"]), s["date"], d["content"]))
    return docs


def header(sid, date) -> str:
    return f"[周期{sid} | 日期 {date}] "


def build_embed_memory(docs: list, workers: int = 8) -> EmbedMemory:
    """并行 embed 所有 doc(带周期/日期表头),再按序装进 EmbedMemory。
    串行 117 篇连发曾因单次死 socket 整轮挂起;并行(pmap)+ 单次超时(见 embed_texts)双保险,
    且 ~5× 提速。检索 order-independent,装入顺序不影响正确性。"""
    mem = EmbedMemory(chunk=True)
    mem.reset()

    def _embed_one(item):
        sid, date, content = item
        pieces = [header(sid, date) + p for p in (_chunk(content, mem.chunk_chars) if mem.chunk else [content]) if p]
        return (sid, pieces, embed_texts(pieces, mem.model)) if pieces else None

    for r in config.pmap(_embed_one, docs, workers=workers):
        if not r:
            continue
        sid, pieces, vecs = r
        for piece, v in zip(pieces, vecs):
            mem._docs.append(piece)
            mem._meta.append({"period_idx": sid, "doc_id": f"s{sid}"})
            mem._vecs.append(v)
    return mem


def build_full_context(docs: list, budget: int = FULLCTX_CHAR_BUDGET) -> tuple:
    """全部 doc 拼成一个大上下文(带表头)。超预算则按 session 升序贪心截断。
    返回 (context_text, truncated: bool, used_chars: int)。"""
    blocks = [header(sid, date) + content for sid, date, content in docs]
    full = "\n\n".join(blocks)
    if len(full) <= budget:
        return full, False, len(full)
    # 贪心保留靠前(早周期)文档,截到预算内
    kept, used = [], 0
    for b in blocks:
        if used + len(b) + 2 > budget:
            break
        kept.append(b)
        used += len(b) + 2
    return "\n\n".join(kept), True, used


# ─────────────────────────────────────────────────────────────────────────────
# 系统 B:FullContextRAG —— 全语料一次性作答(与 r1 同款极简 system prompt)
# ─────────────────────────────────────────────────────────────────────────────
def fullctx_answer(question: str, context: str, max_tokens: int = QA_MAX_TOKENS) -> str:
    """全上下文单轮合成。复用 R1_SYSTEM 保证与 A 同一作答风格,只换'检索片段'→'全部资料'。"""
    msgs = [
        {"role": "system", "content": R1_SYSTEM},
        {"role": "user", "content": (
            f"【全部资料】\n{context}\n\n"
            f"【问题】{question}\n\n"
            f"请只给最终答案(极简):"
        )},
    ]
    # reasoning 模型偶发空正文(token 被 reasoning 吃光 / 僵尸 socket)→ 重试一次
    for attempt in range(2):
        try:
            ans = (config.chat(msgs, temperature=0.0, max_tokens=max_tokens) or "").strip()
            if ans:
                return ans
        except Exception as e:
            if attempt == 0:
                continue
            return f"[FULLCTX_ERROR:{type(e).__name__}]"
    return ans  # 二次仍空则返回空串(judge 会判错,不致崩)


# ─────────────────────────────────────────────────────────────────────────────
# 系统 C:IterativeRAG —— 检索 → 抽桥实体 → 再检索 → 合成(L2 多跳所需)
# ─────────────────────────────────────────────────────────────────────────────
HOP1_SYSTEM = """你在做多跳检索的【第一跳】。给定一个最终问题和一批检索片段,\
该问题需要先定位一个【中间实体】(桥),才能继续查到最终答案。

【任务】只根据片段,找出回答最终问题所必需的那个中间实体(通常是人名/部门/职位)。
【输出严格 JSON】{"bridge": "中间实体值", "subquestion": "用该中间实体改写出的、用于第二跳检索的子问题"}
若片段里找不到中间实体,bridge 填空串。"""


def iterative_answer(question: str, mem: EmbedMemory, top_k: int = TOP_K,
                     max_tokens: int = QA_MAX_TOKENS) -> dict:
    """两跳:retrieve → LLM 抽桥+改写子问 → 用子问再 retrieve → 合并片段合成。
    返回 {pred, bridge, snippets1, snippets2}(诊断用)。"""
    snip1 = mem.retrieve(question, top_k=top_k)
    ctx1 = "\n\n".join(f"[片段{i+1}] {s}" for i, s in enumerate(snip1)) or "(无检索结果)"
    msgs = [
        {"role": "system", "content": HOP1_SYSTEM},
        {"role": "user", "content": (
            f"【检索片段】\n{ctx1}\n\n【最终问题】{question}\n\n严格 JSON:"
        )},
    ]
    bridge, subq = "", ""
    try:
        data = config.chat_json(msgs, temperature=0.0, max_tokens=max_tokens)
        bridge = str(data.get("bridge", "") or "").strip()
        subq = str(data.get("subquestion", "") or "").strip()
    except Exception:
        pass

    # 第二跳:用桥实体/子问拉第二批片段(取不到桥就退化成对原问再检索)
    hop2_query = subq or (f"{bridge} {question}" if bridge else question)
    snip2 = mem.retrieve(hop2_query, top_k=top_k)

    merged = list(dict.fromkeys(snip1 + snip2))  # 去重保序,两跳证据合并
    pred = r1_answer(question, merged, max_tokens=max_tokens)
    return {"pred": pred, "bridge": bridge, "subquestion": subq,
            "n_snip1": len(snip1), "n_snip2": len(snip2), "n_merged": len(merged)}


# ─────────────────────────────────────────────────────────────────────────────
# 单系统跑评(并行)
# ─────────────────────────────────────────────────────────────────────────────
def _gold_or_skip(q: dict):
    """返回 (gold_set, judgeable)。空金串 = 不可判分,剔除。"""
    gs = gold_strings(q)
    return gs, bool(gs)


def run_system(name: str, questions: list, docs: list, workers: int = WORKERS,
               verbose: bool = True, shared_mem=None) -> list:
    """对一个系统跑全部题,返回 records(每题一条,含 pred/correct/judgeable)。
    shared_mem:A/C 共享的 EmbedMemory(索引相同,外部建一次传入 → 省一半 embedding 调用)。"""
    name = name.upper()
    if verbose:
        print(f"\n[{name}] ingest + 提问 {len(questions)} 题 ...")

    # ── 1) 预备各系统的"语料态" ──
    mem = None
    full_ctx = None
    trunc_info = None
    if name in ("A", "C"):
        if shared_mem is not None:
            mem = shared_mem
            if verbose:
                print(f"[{name}] 复用共享 EmbedMemory ({len(mem._docs)} chunks)")
        else:
            t = time.time()
            mem = build_embed_memory(docs)
            if verbose:
                print(f"[{name}] EmbedMemory ingest 完成 ({time.time()-t:.1f}s, "
                      f"{len(mem._docs)} chunks)")
    if name == "B":
        full_ctx, truncated, used = build_full_context(docs)
        trunc_info = {"truncated": truncated, "used_chars": used}
        if verbose:
            tag = f"截断到 {used} 字符" if truncated else f"全量 {used} 字符(未截断)"
            print(f"[{name}] FullContext 上下文: {tag}")

    # ── 2) 每题的求解函数(题与题独立 → pmap 并行) ──
    def solve(q: dict) -> dict:
        gold_set, judgeable = _gold_or_skip(q)
        rec = {
            "line": q["line"], "capability": q["capability"],
            "question": q["question"], "gold_set": gold_set,
            "judgeable": judgeable,
        }
        if name == "A":
            snips = mem.retrieve(q["question"], top_k=TOP_K)
            rec["pred"] = r1_answer(q["question"], snips, max_tokens=QA_MAX_TOKENS)
        elif name == "B":
            rec["pred"] = fullctx_answer(q["question"], full_ctx)
        elif name == "C":
            r = iterative_answer(q["question"], mem)
            rec["pred"] = r["pred"]
            rec["bridge_extracted"] = r["bridge"]
        else:
            raise ValueError(f"未知系统: {name}")
        return rec

    records = config.pmap(solve, questions, workers=workers)

    # ── 3) 判分(也并行;失败标记但不崩) ──
    def do_judge(rec: dict) -> dict:
        if not rec["judgeable"]:
            rec["correct"] = None
            return rec
        pred = rec["pred"]
        if isinstance(pred, str) and pred.startswith("[") and "ERROR" in pred:
            rec["correct"] = False
            rec["error"] = pred
            return rec
        try:
            rec["correct"] = bool(judge(rec["question"], rec["gold_set"], pred, use_llm=True))
        except Exception as e:
            rec["correct"] = False
            rec["judge_error"] = f"{type(e).__name__}:{str(e)[:60]}"
        return rec

    records = config.pmap(do_judge, records, workers=workers)

    if trunc_info:
        for r in records:
            r["_fullctx"] = trunc_info

    if verbose:
        for r in records:
            mark = "∅" if r["correct"] is None else ("✓" if r["correct"] else "✗")
            extra = ""
            if "bridge_extracted" in r:
                extra = f"  [桥={r['bridge_extracted'][:12]!r}]"
            gold = r["gold_set"] if r["judgeable"] else "(不可判分)"
            print(f"  {mark} [{r['line'][:2]}/{r['capability']}] "
                  f"pred={str(r['pred'])[:28]!r} gold={gold}{extra}")
    return records


# ─────────────────────────────────────────────────────────────────────────────
# 聚合 + 报表
# ─────────────────────────────────────────────────────────────────────────────
def aggregate(records: list) -> dict:
    """按 line / capability 聚合。只统计 judgeable 题。"""
    jr = [r for r in records if r.get("judgeable")]
    n_unjudge = len(records) - len(jr)

    def acc(rs):
        rs = [r for r in rs if r.get("judgeable")]
        if not rs:
            return {"n": 0, "correct": 0, "acc": None}
        c = sum(1 for r in rs if r["correct"])
        return {"n": len(rs), "correct": c, "acc": round(c / len(rs), 3)}

    by_line = {ln: acc([r for r in jr if r["line"] == ln])
               for ln in sorted({r["line"] for r in jr})}
    by_cap = {cap: acc([r for r in jr if r["capability"] == cap])
              for cap in sorted({r["capability"] for r in jr})}
    return {
        "overall": acc(jr),
        "by_line": by_line,
        "by_capability": by_cap,
        "n_unjudgeable": n_unjudge,
    }


# 终端 ANSI 颜色(per-capability 着色:绿=高,黄=中,红=低)
def _color_acc(a):
    if a is None:
        return "  -  "
    s = f"{a:5.0%}"
    if a >= 0.75:
        return f"\033[92m{s}\033[0m"   # 绿
    if a >= 0.40:
        return f"\033[93m{s}\033[0m"   # 黄
    return f"\033[91m{s}\033[0m"       # 红


def print_table(results: dict, sys_names: list):
    print("\n" + "=" * 78)
    print("=== 多系统评测结果(office_v3 | L1_timeline vs L2_relational)===")
    print("=" * 78)

    def cell(d):
        a = d.get("acc")
        return f"{_color_acc(a)} (n={d['n']})" if a is not None else f"  -   (n=0)"

    # 主表:系统 × {L1, L2, overall}
    hdr = f"{'系统':<18} {'L1_timeline':<22} {'L2_relational':<22} {'overall':<18}"
    print(hdr)
    print("-" * 78)
    for s in sys_names:
        agg = results[s]["agg"]
        l1 = agg["by_line"].get("L1_timeline", {"acc": None, "n": 0})
        l2 = agg["by_line"].get("L2_relational", {"acc": None, "n": 0})
        ov = agg["overall"]
        label = f"{s} = {SYSTEM_LABELS.get(s, s)}"
        print(f"{label:<18} {cell(l1):<31} {cell(l2):<31} {cell(ov):<27}")

    # 副表:L1 内 per-capability(着色)
    print("\n--- L1_timeline 内 per-capability accuracy(着色:绿≥75% 黄≥40% 红<40%)---")
    caps = [c for c in L1_CAPS_ORDER
            if any(c in results[s]["agg"]["by_capability"] for s in sys_names)]
    head = f"{'系统':<14}" + "".join(f"{c:<10}" for c in caps)
    print(head)
    print("-" * (14 + 10 * len(caps)))
    for s in sys_names:
        bc = results[s]["agg"]["by_capability"]
        row = f"{s:<14}"
        for c in caps:
            d = bc.get(c)
            row += f"{_color_acc(d['acc'] if d else None):<19}"
        print(row)

    # 排名翻转判定
    print("\n--- ★ 排名翻转判定 ---")
    verdict = ranking_flip_verdict(results, sys_names)
    print(verdict["text"])


SYSTEM_LABELS = {"A": "SingleShotRAG", "B": "FullContextRAG", "C": "IterativeRAG"}


def ranking_flip_verdict(results: dict, sys_names: list) -> dict:
    """判定排名翻转:A 在 L1 领先、而 B/C 在 L2 反超 A。"""
    def la(s, ln):
        d = results[s]["agg"]["by_line"].get(ln)
        return d["acc"] if d and d["acc"] is not None else None

    a_l1 = la("A", "L1_timeline")
    a_l2 = la("A", "L2_relational")
    lines = []
    flip = False
    if "A" in sys_names:
        for s in sys_names:
            if s == "A":
                continue
            s_l1, s_l2 = la(s, "L1_timeline"), la(s, "L2_relational")
            if None in (a_l1, a_l2, s_l1, s_l2):
                continue
            l1_lead = a_l1 >= s_l1            # A 在 L1 不输于该系统
            l2_super = s_l2 > a_l2            # 该系统在 L2 反超 A
            lines.append(
                f"  A vs {s}: L1[A={a_l1:.0%} {'≥' if l1_lead else '<'} {s}={s_l1:.0%}] | "
                f"L2[{s}={s_l2:.0%} {'>' if l2_super else '≤'} A={a_l2:.0%}]  "
                f"→ {'翻转✓' if (l1_lead and l2_super) else '未翻转'}"
            )
            flip = flip or (l1_lead and l2_super)
    text = "\n".join(lines) if lines else "  (无足够系统对比)"
    head = ("✓ 排名翻转出现:存在系统在 L1 不胜 A、却在 L2 反超 A → 区分度成立。"
            if flip else
            "✗ 未观察到清晰排名翻转(见下方逐对对比;可能受样本量/judge 噪声影响)。")
    return {"flip": flip, "text": head + "\n" + text}


def write_report(results: dict, sys_names: list, meta: dict):
    """写 markdown 报告:主表 + per-capability + 解读 + caveat。"""
    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)

    def mdacc(d):
        a = d.get("acc") if d else None
        return f"{a:.0%} (n={d['n']})" if (d and a is not None) else "— (n=0)"

    L = []
    L.append("# 多系统记忆评测报告 — office_v3\n")
    L.append(f"- 评测集:`{meta['bench']}`(L1_timeline {meta['n_l1']} 题 / "
             f"L2_relational {meta['n_l2']} 题,可判分 {meta['n_judgeable']} 题;"
             f"ORDER {meta['n_order']} 题按 Kendall-τ 单算、未计入主表)")
    L.append(f"- 语料:`{meta['corpus']}`({meta['n_sessions']} sessions / "
             f"{meta['n_docs']} docs / {meta['corpus_chars']} 字符)")
    L.append(f"- 模型:`{config.MODEL}`(DeepSeek 推理模型,DMXAPI)| QA/judge temperature=0\n")

    L.append("## 系统")
    for s in sys_names:
        L.append(f"- **{s} = {SYSTEM_LABELS.get(s, s)}** — {SYSTEM_DESC.get(s, '')}")
    L.append("")

    # 主表
    L.append("## 主表:line × 系统 accuracy\n")
    L.append("| 系统 | L1_timeline | L2_relational | overall |")
    L.append("|---|---|---|---|")
    for s in sys_names:
        agg = results[s]["agg"]
        l1 = agg["by_line"].get("L1_timeline")
        l2 = agg["by_line"].get("L2_relational")
        ov = agg["overall"]
        L.append(f"| {s} = {SYSTEM_LABELS.get(s, s)} | {mdacc(l1)} | {mdacc(l2)} | {mdacc(ov)} |")
    L.append("")

    # per-capability(L1)
    caps = [c for c in L1_CAPS_ORDER
            if any(c in results[s]["agg"]["by_capability"] for s in sys_names)]
    L.append("## L1_timeline 内 per-capability accuracy\n")
    L.append("| 系统 | " + " | ".join(caps) + " |")
    L.append("|" + "---|" * (len(caps) + 1))
    for s in sys_names:
        bc = results[s]["agg"]["by_capability"]
        row = [s] + [mdacc(bc.get(c)) for c in caps]
        L.append("| " + " | ".join(row) + " |")
    L.append("")

    # 排名翻转
    verdict = ranking_flip_verdict(results, sys_names)
    L.append("## 排名翻转判定(区分度终极证据)\n")
    plain = verdict["text"].replace("✓", "[是]").replace("✗", "[否]")
    for ln in plain.splitlines():
        L.append(ln.strip() if ln.strip().startswith("A vs") else ln)
    L.append("")

    # 解读 + caveat(自动 + 模板)
    L.append("## 解读\n")
    for s in L_interpret(results, sys_names):
        L.append(s)
    L.append("")
    L.append("## Caveat(诚实声明)\n")
    L.append(f"- **L2 样本极小**:仅 {meta['n_l2']} 题多跳,单题翻转即可改变结论,统计意义弱,"
             "结果应视为定性信号而非定量。")
    L.append("- **LLM-judge 噪声**:judge 走同款推理模型先字面后语义兜底,极简答案/同义表述可能误判,"
             "temperature=0 仅降低但不消除抖动。")
    fc = next((results[s]["agg"] for s in sys_names if s == "B"), None)
    trunc = meta.get("fullctx_truncated")
    if trunc is not None:
        L.append(f"- **FullContext 截断**:全语料 {meta['corpus_chars']} 字符,"
                 f"预算 {FULLCTX_CHAR_BUDGET} 字符 → "
                 + ("**已截断**(靠后周期文档被丢弃,B 在依赖晚期证据的题上会偏低)。"
                    if trunc else "**未截断**(B 拿到完整语料,是真·无检索瓶颈上界)。"))
    L.append("- **单链 / ONE world**:一次 ingest 问全部题,无跨链泛化检验。")
    L.append(f"- **FORGET/CONFLICT/ORDER 不计入主表**:这三类是结构化/排序判定(布尔、"
             "'信息不足'、有序事件),非金串匹配范式,本 harness 用 gold_strings 判不了,已剔除。")

    OUT_REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\n[报告] 已写入 {OUT_REPORT}")


SYSTEM_DESC = {
    "A": "EmbedMemory(top_k=3) 检索 → r1 单轮合成。最朴素 RAG;紧检索在 L1 避开信号竞争噪声,"
         "但取不到被隐藏的多跳桥文档。",
    "B": "全部 docs(带 [周期|日期] 表头)塞进一个 prompt 一次性作答。无检索瓶颈上界;"
         "L2 多跳能直接看到两端证据,L1 反而要在全量噪声里挑信号。",
    "C": "retrieve → LLM 抽桥实体并改写子问 → 用桥再 retrieve → 合并两跳证据合成。"
         "L2 多跳真正需要的架构。",
}


def L_interpret(results: dict, sys_names: list) -> list:
    """生成 3~5 句自动解读(基于实测数字)。"""
    def la(s, ln):
        d = results[s]["agg"]["by_line"].get(ln)
        return d["acc"] if d and d["acc"] is not None else None

    out = []
    a_l1, a_l2 = la("A", "L1_timeline"), la("A", "L2_relational")
    if a_l1 is not None and a_l2 is not None:
        out.append(f"1. **SingleShotRAG(A)** L1={a_l1:.0%} / L2={a_l2:.0%}:"
                   + ("L2 显著低于 L1,符合'单跳检索取不到被隐藏桥文档'的预期。"
                      if a_l2 < a_l1 else "L1/L2 差距不大。"))
    for s in sys_names:
        if s == "A":
            continue
        s_l1, s_l2 = la(s, "L1_timeline"), la(s, "L2_relational")
        if None in (s_l1, s_l2, a_l2):
            continue
        out.append(f"2. **{SYSTEM_LABELS.get(s, s)}({s})** L1={s_l1:.0%} / L2={s_l2:.0%}:"
                   + (f"L2 相对 A({a_l2:.0%})反超 {s_l2 - a_l2:+.0%},"
                      "印证更强架构专补多跳能力层。"
                      if s_l2 > a_l2 else f"L2 未反超 A({a_l2:.0%})。"))
    verdict = ranking_flip_verdict(results, sys_names)
    out.append("3. **结论**:" + ("观察到排名翻转 → 该 benchmark 能按 L1/L2 能力层级区分"
               "记忆系统架构,区分度实测成立。"
               if verdict["flip"] else "未观察到清晰排名翻转 → 在当前系统集/样本量下,"
               "L1/L2 的架构区分信号不显著(见 caveat)。"))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="多系统记忆评测(排名翻转区分度验证)")
    ap.add_argument("--bench", default=str(DEFAULT_BENCH))
    ap.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    ap.add_argument("--systems", default="A,B,C", help="逗号分隔,如 A,B 或 A,B,C")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--smoke", action="store_true", help="烟测:每 line 取少量题快速验通管线")
    args = ap.parse_args()

    sys_names = [s.strip().upper() for s in args.systems.split(",") if s.strip()]

    all_q = json.loads(Path(args.bench).read_text(encoding="utf-8"))
    # ORDER 单列(本轮不进主表)
    order_q = [q for q in all_q if q["capability"] in SKIP_CAPABILITIES]
    questions = [q for q in all_q if q["capability"] not in SKIP_CAPABILITIES]

    if args.smoke:
        # 每 line 取 1 题 L1 + 全部(≤2) L2,验通管线
        l1 = [q for q in questions if q["line"] == "L1_timeline"][:1]
        l2 = [q for q in questions if q["line"] == "L2_relational"][:2]
        questions = l1 + l2
        print(f"[SMOKE] 烟测:{len(questions)} 题 × 系统 {sys_names}")

    docs = load_corpus(args.corpus)
    corpus_chars = sum(len(header(s, d)) + len(c) for s, d, c in docs)
    n_l1 = sum(1 for q in questions if q["line"] == "L1_timeline")
    n_l2 = sum(1 for q in questions if q["line"] == "L2_relational")

    print(f"[multi_system] bench={args.bench}")
    print(f"[multi_system] 题:L1={n_l1} L2={n_l2} (ORDER {len(order_q)} 题单列) | "
          f"语料 {len(docs)} docs / {corpus_chars} 字符 | 系统={sys_names}")

    # A/C 共享同一份 EmbedMemory(索引相同,只建一次 → 省一半 embedding 调用、也少踩死 socket)
    shared_mem = None
    if any(s in ("A", "C") for s in sys_names):
        print("[multi_system] 预建共享 EmbedMemory(A/C 复用)...")
        t0 = time.time()
        shared_mem = build_embed_memory(docs)
        print(f"[multi_system] 共享 EmbedMemory ready "
              f"({time.time()-t0:.1f}s, {len(shared_mem._docs)} chunks)")

    results, judgeable_n = {}, None
    fullctx_truncated = None
    for s in sys_names:
        t = time.time()
        recs = run_system(s, questions, docs, workers=args.workers, shared_mem=shared_mem)
        agg = aggregate(recs)
        results[s] = {"records": recs, "agg": agg}
        judgeable_n = agg["overall"]["n"]   # 可判分题数(各系统一致)
        if s == "B":
            fc = next((r.get("_fullctx") for r in recs if r.get("_fullctx")), None)
            fullctx_truncated = fc["truncated"] if fc else None
        print(f"[{s}] 完成 ({time.time()-t:.1f}s) "
              f"overall={agg['overall']['acc']} "
              f"L1={agg['by_line'].get('L1_timeline', {}).get('acc')} "
              f"L2={agg['by_line'].get('L2_relational', {}).get('acc')}")

    # 打印表
    print_table(results, sys_names)

    # 落盘 JSON
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    dump = {
        "bench": args.bench, "corpus": args.corpus, "model": config.MODEL,
        "systems": sys_names, "top_k": TOP_K, "fullctx_char_budget": FULLCTX_CHAR_BUDGET,
        "fullctx_truncated": fullctx_truncated,
        "n_l1": n_l1, "n_l2": n_l2, "n_order_excluded": len(order_q),
        "results": {s: {"agg": results[s]["agg"],
                        "records": results[s]["records"]} for s in sys_names},
        "ranking_flip": ranking_flip_verdict(results, sys_names),
    }
    OUT_JSON.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[结果] 已写入 {OUT_JSON}")

    # 报告
    meta = {
        "bench": args.bench, "corpus": args.corpus,
        "n_l1": n_l1, "n_l2": n_l2, "n_order": len(order_q),
        "n_judgeable": judgeable_n,
        "n_sessions": len({s for s, _, _ in docs}),
        "n_docs": len(docs), "corpus_chars": corpus_chars,
        "fullctx_truncated": fullctx_truncated,
    }
    write_report(results, sys_names, meta)


if __name__ == "__main__":
    main()
