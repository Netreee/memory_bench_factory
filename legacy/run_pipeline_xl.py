"""
pipeline.run_pipeline_xl — V11-XL 大规模语料合成(电商 SKU 目录场景,全 LLM,过夜可续跑)。

设计(见对话 V11-XL 章节):
  - 世界层:分批 LLM 造 ~N 个 SKU(单次 Stage C 塞不下)→ merge → assemble_world(代码、可编程 gt)。
  - 信号层:mention-on-change 逐周渲染,facts 按实体分块(防单次超 max_tokens)。
  - 草堆层:逐周多批 LLM 生成 filler,双黑名单防串扰/泄漏。
  - ★ 逐周 checkpoint(原子写),挂了 --resume 从上周续;QA(出题/四闸)解耦,之后单跑。

用法:
  nohup ./venv/bin/python -u -m pipeline.run_pipeline_xl --entities 100 --weeks 104 \
        --filler-per-week 80 > /private/tmp/v11xl.log 2>&1 &
  # 挂了/续跑(同参数即可,自动跳过已完成周):
  ./venv/bin/python -m pipeline.run_pipeline_xl --resume ...
"""
from __future__ import annotations
from pathlib import Path
import argparse
import json
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from pipeline.world_state import (
    assemble_world, WorldState, _date_of, _to_num, EXPIRE, DELETE,
)

OUT_DIR = Path(__file__).resolve().parent.parent / "output"
LEAK_BANNED = ["当前", "现在", "最新", "目前", "截至目前", "迄今", "至今", "一直", "历来",
               "维持", "保持不变", "累计", "现任", "如今", "始终", "仍为", "仍是", "依旧"]

# ── 电商场景 ───────────────────────────────────────────────────────────────
SCENARIO_DESC = (
    "评测记忆体在『电商平台·商品(SKU)目录运营跟踪』场景的表现。运营 leader 跨多周(按周快照)"
    "跟踪大量在售 SKU 的售价、库存量、用户评分、所属类目、促销状态、运营负责人、上架/下架状态等"
    "随时间演化的字段:售价有涨有跌(大促/调价)、库存波动、评分升降、负责人轮换、部分 SKU 会下架停售,"
    "要以最新一周快照为准。")

CATEGORIES = ["女装服饰", "男装鞋靴", "手机数码", "家用电器", "美妆个护", "食品生鲜",
              "母婴玩具", "家居百货", "运动户外", "图书文娱", "汽车用品", "宠物生活"]

WORLD_XL_SYSTEM = """你是 memory benchmark 的 ground-truth 世界设计师。为【电商商品(SKU)目录】场景\
设计一批【随周演化的真值 SKU 表】。代码会据此【机械算标准答案】,你只【填表】——\
★ 严禁写问题/答案,严禁写"最新/当前/截至…仍为"这类结论性措辞。

【每个 SKU = 一个 entity,字段(每个标 type)】
- 数值演化(evolving):售价(元)、库存量(件)、用户评分(0-5)——给 trajectory
  · ★ 数值轨迹必须【非单调】:做峰/谷/反弹(如 售价 199→159→219→189),最大或最小值落在【非首非尾】某周
- 类别/状态演化(evolving):促销状态(如 无/满减/限时折扣/聚划算)、运营负责人(人名)
- 稳定(stable):所属类目、SKU编号 等给单一 value
- ★ 至少 1 个数值字段在 trajectory 末尾 null 结尾(=该 SKU 下架/停售,考遗忘 FORGET/停售前最后值)

【硬约束】
1. 种子扰动:用全新虚构的商品名/品牌/人名/数值,别照搬常见真实品牌
2. session 一律用 0..N-1 的整数,不要写日期
3. evolving 字段至少 2 个不同值;每个 SKU 4-6 个字段
4. ★本批所有 SKU 都属于给定【类目】,商品名带该类目特征;实体名(商品名)务必互不相同、够具体

【输出严格 JSON】(不要 markdown):
{"entities":[
  {"name":"软糯云朵针织开衫(米白)","type":"SKU","fields":{
     "售价":{"type":"evolving","value_type":"元","trajectory":[{"session":0,"value":"199"},{"session":2,"value":"159"},{"session":4,"value":"219"},{"session":6,"value":"189"}]},
     "库存量":{"type":"evolving","value_type":"件","trajectory":[{"session":0,"value":"1200"},{"session":3,"value":"480"},{"session":5,"value":"760"}]},
     "用户评分":{"type":"evolving","value_type":"分","trajectory":[{"session":0,"value":"4.6"},{"session":4,"value":"4.8"},{"session":7,"value":"4.7"}]},
     "促销状态":{"type":"evolving","trajectory":[{"session":0,"value":"无"},{"session":2,"value":"限时折扣"},{"session":6,"value":"聚划算"}]},
     "运营负责人":{"type":"evolving","value_type":"人名","trajectory":[{"session":0,"value":"郑明轩"},{"session":4,"value":"苏婉清"}]},
     "所属类目":{"type":"stable","value":"女装服饰"}}}]}"""

CORPUS_XL_SYSTEM = """你是电商运营语料合成专家。给你【某一周各 SKU 的当期字段值】,合成 1-2 篇\
【详尽】异质文档(运营周报/商品快照/调价通知/选品会纪要),把这些值自然叙述进去、写厚写满。

【硬约束(防剧透,极重要)】
1. ★ 只写【本期快照值】。严禁"当前/现在/最新/目前/截至目前/一直/维持/累计/现任/仍为"这类全局口径词——读一篇就知最新值会破坏跨期记忆考核。
2. ★ 严禁回顾历史值/展望未来/叙述"由X变为Y"的过程。每篇只陈述这一周状态。
3. 正文含【本期日期锚点】(如"2025-01-20 当周")。
4. 若某 SKU 本期标 stopped,自然写明"该 SKU 自本期起下架停售"(★不要写它过去的数值/售价)。
5. 信息【分散】到多篇(价格库存类归运营周报、人事类归通报/邮件),不要一篇全包。
6. ★ 每篇【详尽充实】(1500-2500 字):围绕本期给定 SKU 字段展开(调价动因、库存处置、评分波动归因、负责人交接、促销玩法、下一步等),写厚。
   但★【绝不编造未给定的字段/SKU/数值】,只把给定 facts 写丰满。

【输出严格 JSON】{"docs":[{"type":"运营周报|商品快照|调价通知|选品纪要|邮件","content":"...","fact_refs":["SKU.字段",...]}]}"""

FILLER_XL_SYSTEM = """你生成电商运营团队的【杂项干扰文档】,给记忆评测语料制造"草堆"(干扰)。
题材任选且每篇不同:大促筹备/物流时效公告/客服话术培训/供应商对账会/平台规则更新/仓储盘点通知/\
直播排期/投流预算说明/售后政策/合规提醒/团建/系统维护…

【硬约束】
1. ★ 绝对不碰任何【被追踪的 SKU 名/运营负责人名/字段】(连名字都不要出现,更不能给售价/库存/评分/状态)——只写跟它们【完全无关】的日常杂事。
2. 可出现类目泛称(如"女装类目大促"),但不点具体被追踪 SKU、不给其数值。
3. 自然、像真实电商运营文档,每篇 600-1000 字,正文带【本周日期】。

【输出严格 JSON】{"docs":[{"type":"通知|纪要|公告|邮件|培训","content":"..."}]}"""

FILLER_NAMES = [  # 供 filler 用的中性人名池(冷僻姓,与 LLM 常给的被考负责人名解耦;命中黑名单的会被过滤)
    "费德志", "桑越泽", "练知微", "璩明远", "佴静姝", "万俟澜", "门若谷", "谯予安",
    "鄢长卿", "蒯文琚", "宓清越", "竺怀瑾", "逯安然", "蹇文博", "郗云岫", "司寇朗"]

_NAME_FIELD_HINTS = ("负责", "对接", "运营", "经理", "主管", "买手", "客服")


def _tracked_field_names(ws): return sorted({f for flds in ws.entities.values() for f in flds})


def _tracked_proper_nouns(ws) -> set:
    out = set(ws.entities)
    for flds in ws.entities.values():
        for fname, tl in flds.items():
            if any(h in fname for h in _NAME_FIELD_HINTS):
                for (_s, _d, v) in tl.set_values():
                    if v and _to_num(v) is None and len(str(v)) >= 2:
                        out.add(str(v))
    return out


# ── 世界层:分批造 + merge ───────────────────────────────────────────────────
def build_world_xl(n_entities: int, n_sessions: int, batch: int = 12, log=print) -> WorldState:
    """★小批(12)循环补到够数:reasoning 模型 8192 token 一次装不下太多 SKU,大批会被截断。
    短批/失败自动再来一轮(换类目),直到凑满 n_entities 或达尝试上限。"""
    merged = {"entities": [], "cascades": [], "absent_fields": []}
    seen = set()
    max_attempts = (n_entities + batch - 1) // batch * 2 + 6        # 容失败/短批:加倍 + 缓冲
    attempt = 0
    while len(merged["entities"]) < n_entities and attempt < max_attempts:
        cat = CATEGORIES[attempt % len(CATEGORIES)]
        want = min(batch, n_entities - len(merged["entities"]))
        user = (f"【类目】{cat}\n【本批 SKU 数】{want} 个(都属于「{cat}」类目,商品名互不相同)\n"
                f"【session 数】{n_sessions}(session_id 用 0..{n_sessions-1})\n"
                f"设计 {want} 个该类目的真值 SKU 表,数值非单调、至少 1 个 SKU 末期 null 下架,严格 JSON。")
        try:
            out = config.chat_json(
                [{"role": "system", "content": WORLD_XL_SYSTEM}, {"role": "user", "content": user}],
                temperature=0.7, max_tokens=8192)
        except Exception as e:
            log(f"  [world try {attempt+1}/{max_attempts} {cat}] 失败 {str(e)[:40]};再来一轮"); attempt += 1; continue
        ents = out.get("entities", []) if isinstance(out, dict) else []
        kept = 0
        for e in ents:
            nm = e.get("name")
            if nm and nm not in seen and e.get("fields"):
                seen.add(nm); merged["entities"].append(e); kept += 1
        log(f"  [world try {attempt+1}/{max_attempts} {cat}] +{kept}(累计 {len(merged['entities'])}/{n_entities})")
        attempt += 1
    ws, issues = assemble_world(merged)
    log(f"  ✓ 世界:{len(ws.entities)} SKU / {ws.n_sessions} 周 / issues={len(issues)}")
    return ws


# ── 信号层:逐周渲染(facts 按实体分块)─────────────────────────────────────
def _session_facts(ws: WorldState, s: int) -> list[dict]:
    facts = []
    for ent, flds in ws.entities.items():
        for fname, tl in flds.items():
            op = next((o for o in tl.ops if o.session == s), None)
            if op is None:
                continue
            stopped = op.op in (EXPIRE, DELETE)
            facts.append({"sku": ent, "field": fname,
                          "value": None if stopped else op.value, "stopped": stopped})
    return facts


def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def render_week_signal(ws, s, ents_per_doc=6, log=print) -> tuple[list, list]:
    facts = _session_facts(ws, s)
    if not facts:
        return [], []
    date = _date_of(s)
    # 按 SKU 分组,每组 ~ents_per_doc 个 SKU 一次渲染(防超 max_tokens)
    by_sku = {}
    for f in facts:
        by_sku.setdefault(f["sku"], []).append(f)
    groups = list(_chunk(list(by_sku.items()), ents_per_doc))
    docs, leak = [], []
    for gi, grp in enumerate(groups):
        gfacts = [f for _sku, fs in grp for f in fs]
        base = (f"【第 {s} 周 / {date}】各 SKU 当期字段值(只许写这些、只写本期):\n"
                f"{json.dumps(gfacts, ensure_ascii=False)}\n\n合成 1-2 篇【详尽】文档(每篇 1500-2500 字),严格 JSON。")
        hint = ""
        cur = []
        for _att in range(2):
            try:
                out = config.chat_json(
                    [{"role": "system", "content": CORPUS_XL_SYSTEM},
                     {"role": "user", "content": base + hint}], temperature=0.6, max_tokens=8192)
            except Exception as e:
                leak.append({"session": s, "group": gi, "error": str(e)}); cur = []; break
            cur = out.get("docs", []) if isinstance(out, dict) else []
            hits = sorted({b for d in cur for b in LEAK_BANNED if b in d.get("content", "")})
            if not hits:
                break
            hint = f"\n★ 上版出现全局口径禁词 {hits}(泄漏最新值)。重写:只陈述本期值,不用这类词。"
        for i, d in enumerate(cur):
            d["doc_id"] = f"s{s}_sig{gi}_{i}"
            rem = [b for b in LEAK_BANNED if b in d.get("content", "")]
            if rem:
                leak.append({"session": s, "doc_id": d["doc_id"], "banned": rem})
        docs.extend(cur)
    return docs, leak


# ── 草堆层:逐周多批生成 ─────────────────────────────────────────────────────
def gen_week_filler(s, n_filler, blocked, batch=6, log=print) -> list:
    date = _date_of(s)
    names = [n for n in FILLER_NAMES if n not in blocked]
    out_docs, made = [], 0
    n_calls = (n_filler + batch - 1) // batch
    for ci in range(n_calls):
        want = min(batch, n_filler - made)
        user = (f"【第 {s} 周 / {date}】生成 {want} 篇互不相同的电商运营杂项干扰文档。\n"
                f"★ 可用中性人名(选填):{names}\n"
                f"★ 严禁出现这些被追踪 SKU/负责人/字段名:{sorted(blocked)[:40]}\n严格 JSON。")
        try:
            out = config.chat_json(
                [{"role": "system", "content": FILLER_XL_SYSTEM}, {"role": "user", "content": user}],
                temperature=0.9, max_tokens=8192)
        except Exception:
            continue
        for i, d in enumerate(out.get("docs", []) if isinstance(out, dict) else []):
            c = d.get("content", "")
            if not c or any(b in c for b in blocked):     # 防泄漏/串扰
                continue
            d.update({"doc_id": f"s{s}_filler{ci}_{i}", "is_filler": True, "fact_refs": []})
            out_docs.append(d); made += 1
    return out_docs


# ── checkpoint(原子写)+ resume ────────────────────────────────────────────
def _ckpt_path(tag): return OUT_DIR / f"v11xl_{tag}.json"


def _save_ckpt(tag, bench):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = _ckpt_path(tag)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(bench, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)                       # 原子替换,防写一半挂掉损坏


def _load_ckpt(tag):
    p = _ckpt_path(tag)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def run_xl(tag="ecom", n_entities=100, n_sessions=104, filler_per_week=80, resume=False, log=print):
    t0 = time.time()
    ck = _load_ckpt(tag) if resume else None
    if ck:
        ws = WorldState.from_dict(ck["world_state"])
        corpus = ck["corpus"]
        done = set(ck.get("progress", {}).get("done_weeks", []))
        log(f"=== RESUME {tag}:{len(ws.entities)} SKU / 已完成 {len(done)} 周 / "
            f"现有 {sum(len(x['docs']) for x in corpus['sessions'])} 篇 ===")
    else:
        log(f"=== NEW {tag}:造世界 {n_entities} SKU × {n_sessions} 周 ===")
        ws = build_world_xl(n_entities, n_sessions, log=log)
        if len(ws.entities) < max(10, n_entities // 2):     # ★防呆:世界造废(疑似网络/API故障)→ 当场中止,不浪费整夜
            log(f"✗ 世界 SKU 严重不足({len(ws.entities)}/{n_entities}),疑似网络/API 故障。中止,不写脏 checkpoint。确认网络后重跑。")
            return
        corpus = {"sessions": []}
        done = set()
        _save_ckpt(tag, {"benchmark_id": f"v11xl_{tag}", "scenario": tag,
                         "scenario_desc": SCENARIO_DESC, "world_state": ws.to_dict(),
                         "corpus": corpus, "progress": {"done_weeks": []},
                         "config": {"n_entities": n_entities, "n_sessions": n_sessions,
                                    "filler_per_week": filler_per_week}})
    # ★ 强制语料时间轴 = 请求周数(LLM 轨迹常聚在前几周;草堆要铺满全程,
    #   后段无变更周 = 纯草堆,反而把"几十周前的值"埋更深 → 长程回忆更难)
    ws.n_sessions = max(ws.n_sessions or 0, n_sessions)
    blocked = set(_tracked_field_names(ws)) | _tracked_proper_nouns(ws)
    by_id = {x["session_id"]: x for x in corpus["sessions"]}

    for s in ws.sessions():
        if s in done:
            continue
        sig, _leak = render_week_signal(ws, s, log=log)
        fil = gen_week_filler(s, filler_per_week, blocked, log=log)
        sess = by_id.get(s) or {"session_id": s, "date": _date_of(s), "docs": []}
        sess["docs"] = sig + fil
        by_id[s] = sess
        done.add(s)
        corpus["sessions"] = [by_id[k] for k in sorted(by_id)]
        ck = {"benchmark_id": f"v11xl_{tag}", "scenario": tag, "scenario_desc": SCENARIO_DESC,
              "world_state": ws.to_dict(), "corpus": corpus,
              "progress": {"done_weeks": sorted(done)},
              "config": {"n_entities": n_entities, "n_sessions": n_sessions, "filler_per_week": filler_per_week}}
        _save_ckpt(tag, ck)
        chars = sum(len(dd.get("content", "")) for x in corpus["sessions"] for dd in x["docs"])
        docs = sum(len(x["docs"]) for x in corpus["sessions"])
        log(f"[周 {s}/{ws.n_sessions-1}] +信号{len(sig)} +草堆{len(fil)} | 累计 {docs} 篇 / "
            f"{chars/1e6:.2f}M 字 / ~{chars/1e6:.1f}M token估 | {(time.time()-t0)/60:.0f} 分")
    log(f"=== DONE {tag}:{sum(len(x['docs']) for x in corpus['sessions'])} 篇,用时 {(time.time()-t0)/60:.1f} 分 ===")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="ecom")
    ap.add_argument("--entities", type=int, default=100)
    ap.add_argument("--weeks", type=int, default=104)
    ap.add_argument("--filler-per-week", type=int, default=80)
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()
    run_xl(a.tag, a.entities, a.weeks, a.filler_per_week, a.resume)


if __name__ == "__main__":
    main()
