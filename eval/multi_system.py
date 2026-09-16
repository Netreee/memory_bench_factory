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
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config
from eval.memory_interface import EmbedMemory, _chunk
from eval.embed_cache import cached_embed, cache_size
from eval import qa_cache
from eval.question_filter import export_filtered_benchmark, validate_options as validate_filter_options
from eval.baseline_r1 import unified_answer
from eval.judge import judge, judge_answer, is_judgeable, gold_display, judge_spec, classify_refusal, judge_l2_partial

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

# ── 进度探针(纯旁路,写 _progress.json 供 eval_monitor 只读轮询)──────────────
class _EvalProbe:
    FEED_CAP = 40

    def __init__(self):
        self._lk = threading.Lock()
        self._d = {}
        self._feed = []
        self.eval_id = f"eval_{time.strftime('%Y%m%d-%H%M%S')}"
        self.run_dir = ROOT / "output" / "eval" / self.eval_id
        self._progress = self.run_dir / "_progress.json"

    def _flush(self):
        try:
            self._d["ts"] = time.time()
            self._d["eval_id"] = self.eval_id
            self._d["feed"] = self._feed[-self.FEED_CAP:]
            self.run_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._progress.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._d, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.rename(self._progress)
        except Exception:
            pass

    def start(self, **kw):
        self._d = {"status": "running", "started_ts": time.time(), **kw,
                    "current": "", "embed": {},
                    "sys": {s: {"status": "pending", "done": 0, "judged": 0,
                                "correct": 0, "j_real": 0, "total": kw.get("n_judgeable", 0)}
                            for s in kw.get("systems", [])}}
        self._feed = []
        self._flush()

    def embed_start(self, total):
        self._d["embed"] = {"status": "running", "ts0": time.time(), "done": 0, "total": total}
        self._flush()

    def embed_tick(self, done):
        with self._lk:
            self._d["embed"]["done"] = done
            self._flush()

    def embed_done(self, n_chunks, elapsed):
        self._d["embed"] = {"status": "done", "n_chunks": n_chunks, "elapsed_s": round(elapsed, 1)}
        self._flush()

    def sys_start(self, name, total):
        self._d["current"] = name
        self._d["sys"][name] = {"status": "answering", "done": 0, "judged": 0,
                                 "correct": 0, "j_real": 0, "total": total, "ts0": time.time()}
        self._flush()

    def answer_tick(self, name, i):
        with self._lk:
            self._d["sys"][name]["done"] = i
            self._flush()

    def judge_start(self, name):
        with self._lk:
            self._d["sys"][name]["status"] = "judging"
            self._flush()

    def judge_tick(self, name, i, rec):
        with self._lk:
            sd = self._d["sys"][name]
            sd["judged"] = i
            if rec.get("correct") is not None:
                sd["j_real"] = sd.get("j_real", 0) + 1
                if rec["correct"]:
                    sd["correct"] = sd.get("correct", 0) + 1
            self._feed.append({
                "s": name, "i": i, "ln": (rec.get("line") or "")[:2],
                "cap": rec.get("capability", ""),
                "ok": rec.get("correct"),
                "pred": str(rec.get("pred", ""))[:40],
                "gold": str(rec.get("gold_set", ""))[:40],
            })
            self._flush()

    def sys_done(self, name, agg, elapsed):
        sd = self._d["sys"][name]
        sd["status"] = "done"
        sd["elapsed_s"] = round(elapsed, 1)
        sd["acc"] = agg["overall"].get("acc")
        sd["by_line"] = {ln: d["acc"] for ln, d in agg.get("by_line", {}).items()
                         if d.get("acc") is not None}
        self._flush()

    def done(self, disc=None):
        self._d["status"] = "done"
        self._d["current"] = ""
        self._d["elapsed_s"] = round(time.time() - self._d.get("started_ts", time.time()), 1)
        if disc:
            self._d["disc"] = disc
        self._flush()


# 判分单一真源在 eval/judge.py:judge_spec(按声明的 capability 分派 value/refusal/order)。
# (旧 gold_strings / SKIP_CAPABILITIES / L1_CAPS_ORDER 已废除,避免平行旧真源。)


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


def load_protocol(about_path: Path) -> str:
    """读 00_about.json 的 answer_protocol,渲染成给被测系统的【答题约定】文本。
    这是随题库交付的作答契约(carry-forward / 趋势=首末净方向 / 两类拒答),
    三系统同等注入 → 公平;给规则不等于给答案。读不到则返回空串(不注入)。"""
    try:
        ap = json.loads(Path(about_path).read_text(encoding="utf-8")).get("answer_protocol", {})
    except Exception:
        return ""
    if not ap:
        return ""
    out = ["【答题约定(随题库交付,务必遵守)】"]
    for r in ap.get("rules", []):
        out.append(f"- {r}")
    sm = ap.get("gold_sentinel_map", {})
    if sm:
        out.append(f"- 拒答措辞:从未涉及→『{sm.get('INSUFFICIENT', '无此项')}』;"
                   f"已停统→『{sm.get('forgotten=true', '已停止统计')}』。")
    # 属性归属(L6 拒答题 gold 所依赖的约定;披露=公平测试'能否遵守约定',非泄答案)
    out.append("- 个人/角色不具备案件级属性;问及某实体它本身没有的属性 → 答『无此项/查无』,"
               "不得经关系链折算到关联实体的值。")
    return "\n".join(out)


def build_embed_memory(docs: list, workers: int = 3,
                       on_progress=None) -> EmbedMemory:
    """并行 embed 所有 doc(带周期/日期表头),再按序装进 EmbedMemory。
    对 DMXAPI embedding 抖动有韧性:低并发首轮(降并发连接 → 减少 drop)+ 失败篇【串行补漏】
    (避开并发风暴重试)+ 补到底仍失败才抛(残缺索引污染检索,不静默跳过)。
    检索 order-independent,装入顺序不影响正确性。
    on_progress(done, total): 可选回调,每完成一篇 embed 调一次。"""
    mem = EmbedMemory(chunk=True)
    mem.reset()
    _cnt_lk = threading.Lock()
    _cnt = [0]
    total = len(docs)

    def _prep(item):
        sid, date, content = item
        return [header(sid, date) + p
                for p in (_chunk(content, mem.chunk_chars) if mem.chunk else [content]) if p]

    def _try(item):
        pieces = _prep(item)
        if not pieces:
            with _cnt_lk:
                _cnt[0] += 1
                if on_progress:
                    on_progress(_cnt[0], total)
            return [item, [], []]            # 空文档:无 piece
        try:
            r = [item, pieces, cached_embed(pieces, mem.model)]   # 命中走盘缓存,未命中嵌+落盘
        except Exception:
            return [item, pieces, None]      # 失败标记,稍后串行补
        with _cnt_lk:
            _cnt[0] += 1
            if on_progress:
                on_progress(_cnt[0], total)
        return r

    results = config.pmap(_try, docs, workers=workers)

    # 串行补漏(并发失败的,降速逐篇重试;最多 2 轮)
    for rnd in range(2):
        failed = [r for r in results if r[2] is None]
        if not failed:
            break
        print(f"[embed] 第{rnd+1}轮串行补漏 {len(failed)} 篇 ...")
        for r in failed:
            try:
                r[2] = cached_embed(r[1], mem.model)
            except Exception:
                pass
            else:
                with _cnt_lk:
                    _cnt[0] += 1
                    if on_progress:
                        on_progress(_cnt[0], total)
    still = [r[0][0] for r in results if r[2] is None]
    if still:
        raise RuntimeError(
            f"embed ingest:{len(still)} 篇补漏后仍失败(DMXAPI embedding 不稳),建议稍后重试。失败周期={still[:10]}")

    for item, pieces, vecs in results:
        sid = item[0]
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
# 单系统跑评(并行)
# ─────────────────────────────────────────────────────────────────────────────
def run_system(name: str, questions: list, sys_instance, workers: int = WORKERS,
               verbose: bool = True, protocol: str = "",
               probe=None, bench_id: str = None, resume: bool = True) -> list:
    """对一个系统跑全部题,返回 records(每题一条,含 pred/correct/judgeable)。
    sys_instance:已 ingest 好的 MemorySystem 实例。
    protocol:随题库交付的【答题约定】,同等注入三系统的作答提示。
    bench_id+resume:QA 断点续传——已判过的题从盘加载、跳过(干净结果才入盘,错的下轮重试)。"""
    done = qa_cache.load(bench_id, name) if (resume and bench_id) else {}
    if verbose:
        print(f"\n[{name}] 提问 {len(questions)} 题 ..."
              + (f"(断点续传:已有 {len(done)} 题,本轮跳过)" if done else ""))

    # ── 每题的求解函数(题与题独立 → pmap 并行) ──
    def solve(q: dict) -> dict:
        qh = qa_cache.qhash(q)
        if qh in done:                       # 续传:已判过 → 直接用盘上结果,不调 LLM
            r = dict(done[qh]); r["_qh"] = qh; r["_resumed"] = True
            return r
        mode, _, _ = judge_spec(q)
        rec = {
            "_qh": qh,
            "qid": q.get("qid"),
            "line": q["line"], "capability": q["capability"],
            "question": q["question"], "gt": q.get("gt"),
            "aux": q.get("aux"),                             # ★L6 透传:判分需 aux.lure.value(吐诱饵=判错)
            "strict_scoring": q.get("strict_scoring"),       # 明星题全原子合同必须穿透真实评测调用链
            "gold_set": gold_display(q), "mode": mode,
            "judgeable": is_judgeable(q),
        }
        try:
            context = sys_instance.retrieve(q["question"], top_k=TOP_K)
            rec["pred"] = unified_answer(q["question"], context, protocol=protocol,
                                         max_tokens=QA_MAX_TOKENS)
            diag = sys_instance.get_diagnostics()
            if diag.get("bridge"):
                rec["bridge_extracted"] = diag["bridge"]
        except Exception as e:
            rec["pred"] = f"[SOLVE_ERROR:{type(e).__name__}:{str(e)[:50]}]"
        return rec

    if probe:
        probe.sys_start(name, len(questions))
        _solve0, _slk, _sn = solve, threading.Lock(), [0]
        def solve(q, _f=_solve0):
            r = _f(q)
            with _slk:
                _sn[0] += 1
                probe.answer_tick(name, _sn[0])
            return r

    records = config.pmap(solve, questions, workers=workers)

    # ── 3) 判分(也并行;失败标记但不崩) ──
    def do_judge(rec: dict) -> dict:
        if rec.get("_resumed"):              # 续传来的:已判过,原样用
            return rec
        pred = rec["pred"]
        if not rec["judgeable"]:
            rec["correct"] = None
        elif isinstance(pred, str) and pred.startswith("[") and "ERROR" in pred:
            rec["correct"] = False
            rec["error"] = pred
        else:
            try:
                qd = {"capability": rec["capability"], "gt": rec.get("gt"),
                      "question": rec["question"], "aux": rec.get("aux"),
                      "strict_scoring": rec.get("strict_scoring")}
                rec["correct"] = bool(judge_answer(qd, pred, use_llm=True))
                if rec["capability"] == "L6_refusal":       # ★三分桶(报表用):refuse/lure/other
                    lure = ((rec.get("aux") or {}).get("lure") or {}).get("value")
                    rec["refusal_bucket"] = classify_refusal(pred, lure)
                if rec["capability"] == "L2_multihop":      # ★L2 部分 credit:gt=1.0 / 桥=0.5 / 否则 0
                    rec["partial"] = judge_l2_partial(qd, pred, use_llm=True)
            except Exception as e:
                rec["correct"] = False
                rec["judge_error"] = f"{type(e).__name__}:{str(e)[:60]}"
        # 干净结果才入盘续传;带 error/judge_error 的(端点抖动所致)不存 → 下轮重试
        if bench_id and "error" not in rec and "judge_error" not in rec:
            qa_cache.append(bench_id, name, rec)
        return rec

    if probe:
        probe.judge_start(name)
        _jdg0, _jlk, _jn = do_judge, threading.Lock(), [0]
        def do_judge(rec, _f=_jdg0):
            r = _f(rec)
            with _jlk:
                _jn[0] += 1
                probe.judge_tick(name, _jn[0], r)
            return r

    records = config.pmap(do_judge, records, workers=workers)

    if hasattr(sys_instance, 'trunc_info'):
        ti = sys_instance.trunc_info
        for r in records:
            r["_fullctx"] = ti

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


SYSTEM_LABELS = {
    "A": "SingleShotRAG", "B": "FullContextRAG", "C": "IterativeRAG",
    "mem0": "Mem0", "zep": "Zep", "memos": "MemOS", "amem": "A-Mem",
}
SYSTEM_DESC = {
    "A": "EmbedMemory(top_k=3) 检索 → r1 单轮合成。最朴素 RAG;紧检索易漏被隐藏的多跳桥文档/晚期证据。",
    "B": "全部 docs(带 [周期|日期] 表头)塞进一个 prompt 一次性作答。无检索瓶颈上界;受上下文预算约束。",
    "C": "retrieve → LLM 抽桥实体并改写子问 → 用桥再 retrieve → 合并两跳证据合成(多跳所需架构)。",
    "mem0": "Mem0(Qdrant + LLM fact extraction)。事实三元组自动提取 + 向量检索。",
    "zep": "Zep 时序知识图。对话→时序 fact graph → 语义搜索。支持 CE 自托管或 Cloud。",
    "memos": "MemOS(MemTensor)。多层记忆架构(事实 + 偏好 + 对话)。支持自托管或 Cloud。",
    "amem": "A-Mem(Zettelkasten 图记忆)。LLM 分析→结构化 note(keywords/tags/links)→向量+图扩展检索。",
}
# 七线展示顺序(report/表格)。短码 = ln.split('_')[0](L1..L7)。
LINE_ORDER = ["L1_timeline", "L2_relational", "L3_process", "L4_preference",
              "L5_conflict", "L6_refusal", "L7_consolidation"]


def _present_lines(results: dict, sys_names: list) -> list:
    """出现在结果里的 line,按 LINE_ORDER 排;未登记的兜底追加。"""
    seen = set()
    for s in sys_names:
        seen |= set(results[s]["agg"]["by_line"].keys())
    ordered = [ln for ln in LINE_ORDER if ln in seen]
    return ordered + [ln for ln in sorted(seen) if ln not in LINE_ORDER]


def discrimination_summary(results: dict, sys_names: list) -> dict:
    """区分度摘要:逐线跨系统离差(max-min)+ 总分排名 + 余量(1-最高)。
    benchmark 目的是'考倒记忆 agent' → 既要能分开系统(离差大),又不被某系统刷满(余量大)。"""
    def la(s, ln):
        d = results[s]["agg"]["by_line"].get(ln)
        return d["acc"] if d and d["acc"] is not None else None

    lines = _present_lines(results, sys_names)
    spreads = {}
    for ln in lines:
        accs = [la(s, ln) for s in sys_names]
        accs = [a for a in accs if a is not None]
        spreads[ln] = (max(accs) - min(accs)) if len(accs) >= 2 else None

    overall = {s: (results[s]["agg"]["overall"]["acc"] or 0.0) for s in sys_names}
    ranking = sorted(sys_names, key=lambda s: overall[s], reverse=True)
    ov_vals = [overall[s] for s in sys_names]
    ov_spread = (max(ov_vals) - min(ov_vals)) if len(ov_vals) >= 2 else 0.0
    best = max(ov_vals) if ov_vals else 0.0
    headroom = 1.0 - best

    max_line_spread = max((v for v in spreads.values() if v is not None), default=0.0)
    discriminates = (ov_spread >= 0.10) or (max_line_spread >= 0.20)

    return {"spreads": spreads, "ranking": ranking, "overall": overall,
            "ov_spread": ov_spread, "max_line_spread": max_line_spread,
            "headroom": headroom, "best": best, "discriminates": discriminates,
            "lines": lines}


def print_table(results: dict, sys_names: list):
    lines = _present_lines(results, sys_names)
    short = [ln.split("_")[0] for ln in lines]
    print("\n" + "=" * (20 + 8 * len(lines) + 8))
    print("=== 多系统记忆评测(七线 | bench={} 系统={}) ===".format(
        "/".join(short), ",".join(sys_names)))
    print("=" * (20 + 8 * len(lines) + 8))

    def cell(d):
        return _color_acc(d.get("acc") if d else None)

    hdr = f"{'系统':<16}" + "".join(f"{c:<8}" for c in short) + f"{'overall':<10}"
    print(hdr)
    print("-" * (16 + 8 * len(lines) + 10))
    for s in sys_names:
        agg = results[s]["agg"]
        row = f"{s+'='+SYSTEM_LABELS.get(s, s):<16}"
        for ln in lines:
            row += f"{cell(agg['by_line'].get(ln)):<17}"   # +9 for ANSI codes
        row += f"{cell(agg['overall']):<17}"
        print(row)

    # 逐线题数
    n0 = results[sys_names[0]]["agg"]["by_line"]
    print("\n题数/线: " + "  ".join(
        f"{ln.split('_')[0]}={n0.get(ln, {}).get('n', 0)}" for ln in lines)
        + f"  | 可判分合计={results[sys_names[0]]['agg']['overall']['n']}")

    # per-capability(全 capability,着色)
    caps = sorted({c for s in sys_names for c in results[s]["agg"]["by_capability"]})
    print("\n--- per-capability accuracy(着色:绿≥75% 黄≥40% 红<40%)---")
    print(f"{'系统':<12}" + "".join(f"{c[:13]:<14}" for c in caps))
    for s in sys_names:
        bc = results[s]["agg"]["by_capability"]
        row = f"{s:<12}"
        for c in caps:
            row += f"{_color_acc((bc.get(c) or {}).get('acc')):<23}"
        print(row)

    # 区分度
    ds = discrimination_summary(results, sys_names)
    print("\n--- ★ 区分度摘要 ---")
    print("  总分排名: " + " > ".join(
        f"{s}({ds['overall'][s]:.0%})" for s in ds["ranking"]))
    print(f"  总分离差(max-min)= {ds['ov_spread']:.0%}  |  最高分余量(1-best)= {ds['headroom']:.0%}")
    print("  逐线离差: " + "  ".join(
        f"{ln.split('_')[0]}={(v if v is not None else 0):.0%}"
        for ln, v in ds["spreads"].items()))
    print("  判定: " + ("✓ 有区分度(系统分得开 / 或存在强区分线)"
                        if ds["discriminates"] else
                        "✗ 区分度弱(系统分数贴近;见 caveat)"))


def write_report(results: dict, sys_names: list, meta: dict, out_path=None):
    """写 markdown 报告:七线主表 + per-capability + 区分度 + 解读 + caveat。"""
    out_path = out_path or OUT_REPORT
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def mdacc(d):
        a = d.get("acc") if d else None
        return f"{a:.0%} (n={d['n']})" if (d and a is not None) else "— (n=0)"

    lines = _present_lines(results, sys_names)
    ds = discrimination_summary(results, sys_names)

    L = []
    L.append("# 多系统记忆评测报告 — 七线\n")
    L.append(f"- 评测集:`{meta['bench']}`(可判分 {meta['n_judgeable']}/{meta['n_total']} 题;"
             "七线全判:value=命中值 / refusal=拒答类 / order=时序一致)")
    L.append(f"- 语料:`{meta['corpus']}`({meta['n_sessions']} sessions / "
             f"{meta['n_docs']} docs / {meta['corpus_chars']} 字符)")
    L.append(f"- 协议注入:{'是(随题库交付的答题约定已同等注入三系统)' if meta.get('protocol_injected') else '否'}")
    L.append(f"- 模型:`{config.MODEL}`(DMXAPI)| QA/judge temperature=0\n")

    L.append("## 系统")
    for s in sys_names:
        L.append(f"- **{s} = {SYSTEM_LABELS.get(s, s)}** — {SYSTEM_DESC.get(s, '')}")
    L.append("")

    # 主表:line × 系统
    short = [ln.split("_")[0] for ln in lines]
    L.append("## 主表:line × 系统 accuracy\n")
    L.append("| 系统 | " + " | ".join(short) + " | overall |")
    L.append("|" + "---|" * (len(lines) + 2))
    for s in sys_names:
        agg = results[s]["agg"]
        cells = [mdacc(agg["by_line"].get(ln)) for ln in lines]
        L.append(f"| {s}={SYSTEM_LABELS.get(s, s)} | " + " | ".join(cells)
                 + f" | {mdacc(agg['overall'])} |")
    L.append("")

    # per-capability
    caps = sorted({c for s in sys_names for c in results[s]["agg"]["by_capability"]})
    L.append("## per-capability accuracy\n")
    L.append("| 系统 | " + " | ".join(caps) + " |")
    L.append("|" + "---|" * (len(caps) + 1))
    for s in sys_names:
        bc = results[s]["agg"]["by_capability"]
        row = [s] + [mdacc(bc.get(c)) for c in caps]
        L.append("| " + " | ".join(row) + " |")
    L.append("")

    # 区分度
    L.append("## 区分度判定(benchmark 能否考倒/分开记忆系统)\n")
    L.append("- 总分排名: " + " > ".join(f"{s}({ds['overall'][s]:.0%})" for s in ds["ranking"]))
    L.append(f"- 总分离差(max−min)= **{ds['ov_spread']:.0%}**;最高分余量(1−best)= **{ds['headroom']:.0%}**"
             f"(best={ds['best']:.0%})")
    L.append("- 逐线离差: " + "; ".join(
        f"{ln.split('_')[0]}={(v if v is not None else 0):.0%}" for ln, v in ds["spreads"].items()))
    L.append("- **判定**: " + ("✅ 有区分度——系统总分分得开,或存在强区分线(离差≥20%)。"
                              if ds["discriminates"] else
                              "⚠️ 区分度弱——系统分数贴近(见 caveat:样本量/judge 噪声/协议)。"))
    L.append("")

    # 解读 + caveat
    L.append("## 解读\n")
    for s in L_interpret(results, sys_names):
        L.append(s)
    L.append("")
    L.append("## Caveat(诚实声明)\n")
    L.append("- **每线样本小**:多数线 7~13 题,单题翻转即改变该线百分比,结果为定性信号而非定量。")
    L.append("- **拒答判分从宽**:L6(从未涉及)与 FORGET(已停统)本版都只判'是否拒答而非编造值',"
             "未强制区分两类拒答措辞;故拒答类偏松(测的是'不编造'这一核心技能)。")
    L.append("- **LLM-judge 噪声**:value 先字面后语义兜底,refusal/order 走 LLM 判;temperature=0 降低但不消除抖动。")
    trunc = meta.get("fullctx_truncated")
    if trunc is not None:
        L.append(f"- **FullContext 截断**:全语料 {meta['corpus_chars']} 字符,预算 {FULLCTX_CHAR_BUDGET} → "
                 + ("**已截断**(靠后周期文档被丢弃,B 在依赖晚期证据的题上偏低,非真上界)。"
                    if trunc else "**未截断**(B 拿到完整语料,是真·无检索瓶颈上界)。"))
    L.append("- **单链 / ONE world**:一次 ingest 问全部题,无跨链泛化检验。")
    L.append("- **可插拔架构**:已支持 adapter 模式接入外部记忆系统(mem0/zep/memOS 等)。")

    out_path.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\n[报告] 已写入 {out_path}")


def L_interpret(results: dict, sys_names: list) -> list:
    """基于实测数字生成解读(逐线最强/最弱系统 + 区分度结论)。"""
    def la(s, ln):
        d = results[s]["agg"]["by_line"].get(ln)
        return d["acc"] if d and d["acc"] is not None else None

    ds = discrimination_summary(results, sys_names)
    out = []
    rank_str = " > ".join(f"{s}={ds['overall'][s]:.0%}" for s in ds["ranking"])
    out.append(f"1. **总分排名** {rank_str};最高分 {ds['best']:.0%} → 余量 {ds['headroom']:.0%}"
               + ("(未饱和,bench 仍有难度)。" if ds["headroom"] >= 0.15 else "(接近饱和,bench 偏易)。"))
    # 最强区分线
    valid = {ln: v for ln, v in ds["spreads"].items() if v is not None}
    if valid:
        top_ln = max(valid, key=valid.get)
        accs = {s: la(s, top_ln) for s in sys_names}
        accs = {s: a for s, a in accs.items() if a is not None}
        if accs:
            hi = max(accs, key=accs.get)
            lo = min(accs, key=accs.get)
            out.append(f"2. **最强区分线** {top_ln.split('_')[0]}(离差 {valid[top_ln]:.0%}):"
                       f"{hi}={accs[hi]:.0%} vs {lo}={accs[lo]:.0%} → 不同架构在该能力层分得最开。")
    out.append("3. **结论**: " + ("观察到系统在总分/某些线上分得开 → 该 benchmark 对记忆系统有区分度。"
               if ds["discriminates"] else
               "当前 baseline 集分数贴近 → 区分信号弱(需更强/更弱对照或更大样本,见 caveat)。"))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────
def _load_questions(bench_path: Path) -> list:
    """读题库;兼容 list 与 {questions:[...]} 两种格式。"""
    data = json.loads(Path(bench_path).read_text(encoding="utf-8-sig"))
    return data if isinstance(data, list) else data.get("questions", data)


def main():
    ap = argparse.ArgumentParser(description="多系统记忆评测(七线判分 + 区分度)")
    ap.add_argument("--bench", default=str(DEFAULT_BENCH), help="06_grounded_questions.json")
    ap.add_argument("--corpus", default=str(DEFAULT_CORPUS), help="05_corpus.json")
    ap.add_argument("--about", default="", help="00_about.json(答题协议);默认取 bench 同目录")
    ap.add_argument("--no-protocol", action="store_true", help="不注入答题协议(消融对照)")
    ap.add_argument("--systems", default="A,B,C",
                    help="逗号分隔,如 A,B,C 或 simpleMem,mem0,zep,memos")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--smoke", action="store_true", help="烟测:每 line 取 1 题快速验通管线")
    ap.add_argument("--filter-easy", action="store_true", help="评测后导出 filtered/，默认剔除全员答对题")
    ap.add_argument("--keep-easy-ratio", type=float, default=None, help="全员答对题保留比例(0..1)，同时启用筛选")
    ap.add_argument("--filter-seed", type=int, default=0, help="简单题抽样种子")
    ap.add_argument("--preserve-capability", action="append", default=[], help="筛选时保留该能力全部题，供判分复核；可重复指定")
    args = ap.parse_args()

    sys_names = [s.strip() for s in args.systems.split(",") if s.strip()]
    sys_names = [s.upper() if s.upper() in ("A", "B", "C") else s for s in sys_names]
    filter_enabled = args.filter_easy or args.keep_easy_ratio is not None or bool(args.preserve_capability)
    keep_easy_ratio = args.keep_easy_ratio if args.keep_easy_ratio is not None else 0.0
    if filter_enabled:
        try:
            validate_filter_options(sys_names, keep_easy_ratio, args.filter_seed)
        except ValueError as exc:
            ap.error(str(exc))

    all_q = _load_questions(args.bench)
    questions = [q for q in all_q if is_judgeable(q)]   # 七线全判;弃不可判分(未知题类)
    n_unjudge = len(all_q) - len(questions)

    if args.smoke:
        by_ln = {}
        for q in questions:
            by_ln.setdefault(q["line"], q)   # 每线第一题
        questions = list(by_ln.values())
        print(f"[SMOKE] 烟测:{len(questions)} 题(每线 1)× 系统 {sys_names}")

    bid = qa_cache.bench_id(questions)   # QA 断点续传键(题面集合 hash;题变即换键)

    # 答题协议(随题库交付,同等注入三系统;--no-protocol 关掉做消融)
    about_path = Path(args.about) if args.about else Path(args.bench).parent / "00_about.json"
    protocol = "" if args.no_protocol else load_protocol(about_path)
    proto_on = bool(protocol)

    probe = _EvalProbe()
    probe.start(bench=args.bench, corpus=str(args.corpus), model=config.MODEL,
                systems=sys_names, n_total=len(all_q), n_judgeable=len(questions),
                protocol=proto_on)

    docs = load_corpus(args.corpus)
    corpus_chars = sum(len(header(s, d)) + len(c) for s, d, c in docs)

    import collections as _c
    by_line_n = _c.Counter(q["line"] for q in questions)
    print(f"[multi_system] bench={args.bench}")
    print(f"[multi_system] 可判分 {len(questions)}/{len(all_q)} 题"
          + (f"(弃 {n_unjudge} 不可判分)" if n_unjudge else "")
          + " | 逐线: " + " ".join(f"{ln.split('_')[0]}={by_line_n[ln]}" for ln in LINE_ORDER if ln in by_line_n))
    print(f"[multi_system] 语料 {len(docs)} docs / {corpus_chars} 字符 | 系统={sys_names} | "
          f"协议注入={'是' if proto_on else '否'}")

    # ── 构建记忆系统实例(工厂)──
    from eval.memory_systems import make_system

    # 语料 → session 列表(adapter 接口要求)
    sess_map = {}
    for sid, date, content in docs:
        if sid not in sess_map:
            sess_map[sid] = {"session_id": sid, "date": date, "docs": []}
        sess_map[sid]["docs"].append(content)
    sessions = [sess_map[k] for k in sorted(sess_map)]

    # A/C 共享同一份 EmbedMemory(索引相同,只建一次)
    shared_embed = None
    need_embed = any(s in ("A", "C") for s in sys_names)
    if need_embed:
        print("[multi_system] 预建共享 EmbedMemory(A/C 复用)...")
        probe.embed_start(len(docs))
        t0 = time.time()
        _sm = make_system("A")
        for s_obj in sessions:
            _sm.ingest_session(s_obj)
        _sm.finalize_ingest(on_progress=lambda d, t: probe.embed_tick(d))
        shared_embed = _sm._mem
        probe.embed_done(_sm.chunk_count(), time.time() - t0)
        print(f"[multi_system] 共享 EmbedMemory ready "
              f"({time.time()-t0:.1f}s, {_sm.chunk_count()} chunks)")

    systems = {}
    for s in sys_names:
        if s in ("A", "C") and shared_embed:
            sys_inst = make_system(s, embed_mem=shared_embed)
        else:
            sys_inst = make_system(s)
            for s_obj in sessions:
                sys_inst.ingest_session(s_obj)
            sys_inst.finalize_ingest()
        systems[s] = sys_inst

    results, judgeable_n = {}, None
    fullctx_truncated = None
    for s in sys_names:
        t = time.time()
        recs = run_system(s, questions, systems[s], workers=args.workers,
                          protocol=protocol, probe=probe, bench_id=bid)
        agg = aggregate(recs)
        results[s] = {"records": recs, "agg": agg}
        probe.sys_done(s, agg, time.time() - t)
        judgeable_n = agg["overall"]["n"]
        if s == "B":
            fc = next((r.get("_fullctx") for r in recs if r.get("_fullctx")), None)
            fullctx_truncated = fc["truncated"] if fc else None
        per_line = " ".join(f"{ln.split('_')[0]}={(agg['by_line'].get(ln) or {}).get('acc')}"
                            for ln in LINE_ORDER if ln in agg["by_line"])
        print(f"[{s}] 完成 ({time.time()-t:.1f}s) overall={agg['overall']['acc']} | {per_line}")

    # 打印表
    print_table(results, sys_names)

    # 落盘 JSON(落到本次 eval 独立目录)
    out_json = probe.run_dir / "results.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    dump = {
        "bench": args.bench, "corpus": args.corpus, "model": config.MODEL,
        "systems": sys_names, "top_k": TOP_K, "fullctx_char_budget": FULLCTX_CHAR_BUDGET,
        "fullctx_truncated": fullctx_truncated, "protocol_injected": proto_on,
        "n_total": len(all_q), "n_judgeable": judgeable_n, "n_unjudgeable": n_unjudge,
        "results": {s: {"agg": results[s]["agg"],
                        "records": results[s]["records"]} for s in sys_names},
        "discrimination": discrimination_summary(results, sys_names),
    }
    out_json.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[结果] 已写入 {out_json}")

    # 报告
    meta = {
        "bench": args.bench, "corpus": args.corpus,
        "n_total": len(all_q), "n_judgeable": judgeable_n,
        "n_sessions": len({s for s, _, _ in docs}),
        "n_docs": len(docs), "corpus_chars": corpus_chars,
        "fullctx_truncated": fullctx_truncated, "protocol_injected": proto_on,
    }
    write_report(results, sys_names, meta, out_path=probe.run_dir / "report.md")
    if filter_enabled:
        filter_report = export_filtered_benchmark(
            Path(args.bench), {s: results[s]["records"] for s in sys_names},
            probe.run_dir / "filtered", keep_easy_ratio=keep_easy_ratio, seed=args.filter_seed,
            preserve_capabilities=args.preserve_capability,
            corpus=Path(args.corpus), about=about_path if about_path.is_file() else None,
            result_paths=[out_json])
        fc = filter_report["counts"]
        print(f"[筛题] 全员答对 {fc['all_correct']}，剔除 {fc['removed_easy']}，"
              f"保留 {fc['kept']} → {probe.run_dir / 'filtered'}")
    probe.done(disc=discrimination_summary(results, sys_names))


if __name__ == "__main__":
    main()
