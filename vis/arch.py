"""
vis/arch.py —— 记忆智能体 benchmark 元工厂(oursys)架构图。
直接用 graphviz 手绘(不走 diagrams 库:那库把文字塞图标下方、剪贴画风,text-heavy 算法图丑)。
文字进【圆角框】内(HTML 标签:标题加粗 + 细节小字),统一配色 + 正经图例表 + PingFang SC 中文。

产 3 张图 × (SVG + PDF) 到 vis/:
  1. oursys_pipeline   —— 全 stage 细粒度主流水线
  2. oursys_closedloop —— 闭环驱动 build_to_target 的双环
  3. oursys_triangle   —— 验证三角(题面/答案/语料)+ 三命门

配色语言:紫=LLM调用 / 青=纯代码 / 黄折角=产物json / 橙=闸判定 / 绿=起止 / 红=循环反推。
跑:./venv/bin/python vis/arch.py
"""
import os
os.environ["PATH"] = "/opt/homebrew/bin:" + os.environ.get("PATH", "")   # 让 graphviz 找到 brew 的 dot
import graphviz

F = "PingFang SC"   # macOS 中文字体(fc-list 已确认存在)

PAL = {  # (fill, border, detail-font)
    "llm":  ("#EDE7F6", "#7E57C2", "#5E35B1"),   # LLM 调用
    "code": ("#E0F2F1", "#26A69A", "#00796B"),   # 纯代码
    "art":  ("#FFF8E1", "#FFB300", "#EF6C00"),   # 产物 .json(折角 note)
    "gate": ("#FFE0B2", "#FB8C00", "#E65100"),   # 闸 / 判定
    "se":   ("#DCEDC8", "#7CB342", "#558B2F"),   # 起止
    "loop": ("#FFCDD2", "#E53935", "#C62828"),   # 循环 / 反推
}
EDGE = {
    "flow":   dict(color="#455A64", penwidth="1.7"),
    "branch": dict(color="#6A1B9A", penwidth="1.7"),
    "fb":     dict(color="#C62828", penwidth="1.5", style="dashed", constraint="false"),
    "ok":     dict(color="#2E7D32", penwidth="1.7"),
    "warn":   dict(color="#EF6C00", penwidth="1.7"),
}


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _lab(title, detail, dcolor):
    t = f"<b>{_esc(title)}</b>"
    if not detail:
        return f"<{t}>"
    d = _esc(detail).replace("\n", "<br/>")
    return f'<{t}<br/><font point-size="9" color="{dcolor}">{d}</font>>'


def node(g, nid, title, detail="", kind="code"):
    fill, border, dcolor = PAL[kind]
    if kind == "gate":
        title = "◆ " + title
    shape = "note" if kind == "art" else "box"
    style = "filled" if kind == "art" else "rounded,filled"
    g.node(nid, label=_lab(title, detail, dcolor), shape=shape, style=style,
           fillcolor=fill, color=border, penwidth="1.7")


def edge(g, a, b, label="", kind="flow"):
    g.edge(a, b, label=(" " + label + " " if label else ""), fontsize="10",
           fontcolor="#37474F", **EDGE[kind])


def newgraph(title):
    g = graphviz.Digraph()
    g.attr(rankdir="TB", fontname=F, bgcolor="white", nodesep="0.5", ranksep="0.65",
           splines="spline", pad="0.5", labelloc="b", label=title, fontsize="22", fontcolor="#1A237E")
    g.attr("node", fontname=F, fontsize="11", margin="0.16,0.09")
    g.attr("edge", fontname=F)
    return g


def cattr(title, fill="#F4F7FB", border="#90A4AE"):
    return dict(label=title, style="rounded,filled", fillcolor=fill, color=border,
                fontsize="13", fontcolor="#263238", penwidth="1.4", margin="14")


def legend(g):
    cells = [("#EDE7F6", "LLM 调用"), ("#E0F2F1", "纯代码"), ("#FFF8E1", "产物 .json"),
             ("#FFE0B2", "◆ 闸/判定"), ("#DCEDC8", "起止"), ("#FFCDD2", "循环/反推")]
    tds = "".join(f'<td bgcolor="{c}" width="80" height="22">{_esc(t)}</td>' for c, t in cells)
    lab = ('<<table border="1" color="#B0BEC5" cellborder="0" cellspacing="7" cellpadding="4">'
           f'<tr><td><b>图例</b></td>{tds}</tr></table>>')
    g.node("legend", label=lab, shape="plaintext")


# ════════════════════════════════════════════════════════════════════════════
# 图 1:全 stage 细粒度主流水线
# ════════════════════════════════════════════════════════════════════════════
def build_pipeline():
    g = newgraph("oursys · 全 stage 细粒度流水线")
    legend(g)
    node(g, "scen", "场景 SCENARIOS", "描述 + few_shot · 无 QA", "se")

    with g.subgraph(name="cluster_s0") as c:
        c.attr(**cattr("Stage 0 · input"))
        node(c, "d_in", "00_input.json", "", "art")

    with g.subgraph(name="cluster_s1") as c:
        c.attr(**cattr("Stage 1 · 中央办公室 = 并行议会"))
        node(c, "council", "议会 6 视角(并行 LLM)", "observe · skeptic · map\nmedium · style · traps", "llm")
        node(c, "synth", "综合(代码装配)", "→ active_lines + weights", "code")
        node(c, "polish", "批判润色(LLM)→ 定稿", "", "llm")
        edge(c, "council", "synth"); edge(c, "synth", "polish")

    node(g, "d_wp", "01_whitepaper.json",
         "active_lines+weights · domain_profile\nshared_world_spec · traps", "art")

    with g.subgraph(name="cluster_s2") as c:
        c.attr(**cattr("Stage 2 · 共享世界 build_world   ★命门1(单一真相源)"))
        node(c, "wbatch", "world.batch ×N", "LLM 并发 · ≤4 轮 · merge 去重", "llm")
        node(c, "wasm", "assemble_world", "→ entities / timelines", "code")
        node(c, "wcrit", "W.3 CRITIC 修复轮 ≤3", "validate → world.repair(LLM)\n→ re-assemble", "loop")
        node(c, "wprep", "_prepare_lines(各线叠基质)",
             "L2 +人员 / 软外键链 负责人→人→汇报对象\nL5 inject_conflicts → ws.conflicts", "code")
        edge(c, "wbatch", "wasm"); edge(c, "wasm", "wcrit"); edge(c, "wcrit", "wprep")

    node(g, "d_world", "02_world.json", "entities · timelines · n_sessions · conflicts", "art")

    with g.subgraph(name="cluster_s3") as c:
        c.attr(**cattr("Stage 3 · 产线 run_lines(一分为四)   ★命门2(各线代码 gt)"))
        node(c, "feas", "line.feasible?", "基质不足→跳 · 未建线→跳", "gate")
        node(c, "l1", "L1 时间线", "7能力 IE/KU/TR/MR/\nFORGET/PREEXPIRE/ABS · _CAP_WEIGHT 配比", "code")
        node(c, "l2", "L2 关系多跳", "gt_multihop(path, 周锚)", "code")
        node(c, "l3", "L3 过程排序", "gt_event_order", "code")
        node(c, "l5", "L5 跨源矛盾", "权威值 vs 传闻值", "code")
        for x in ("l1", "l2", "l3", "l5"):
            edge(c, "feas", x)

    node(g, "d_orders", "03_orders.json", "line 标签 · gt · aux · evidence_sessions", "art")

    with g.subgraph(name="cluster_ea") as c:
        c.attr(**cattr("★边 A · 良定义闸 well_posed()    〔设计完成 · 待落地〕", "#F3E5F5", "#AB47BC"))
        node(c, "eaq", "gold 是该题面唯一正确解?",
             "L1 重算/类型/物理 · L2 周锚/链/重算\nL3 唯一可定位/序 · L5 权威/可靠度/非等价", "gate")

    with g.subgraph(name="cluster_s4") as c:
        c.attr(**cattr("Stage 4 · 出题 phrase_questions"))
        node(c, "qint", "line.intent(order)", "→ (intent, 须隐藏)", "code")
        node(c, "qph", "phrase(LLM)只润色措辞", "桥实体 / 答案 绝不进题面", "llm")
        edge(c, "qint", "qph")

    node(g, "d_q", "04_questions.json", "", "art")

    with g.subgraph(name="cluster_s5") as c:
        c.attr(**cattr("Stage 5 · 媒介渲染 render_corpus(逐周并发)"))
        node(c, "cfac", "_session_facts(每周)", "→ 本周变更事实", "code")
        node(c, "csig", "_render_sig 信号文档", "2实体/组 · 4次重渲\n值+实体就近归属 · 滤 LEAK_BANNED", "llm")
        node(c, "ccon", "_render_conflict_docs 小道", "低可信来源 · 不滤 LEAK", "llm")
        node(c, "cfil", "_render_fil 草堆 haystack", "blocked = 被追踪名", "llm")
        node(c, "cck", "ckpt 续渲 + diversity", "NDG / IDO / CR(诊断)", "code")
        for x in ("csig", "ccon", "cfil"):
            edge(c, "cfac", x); edge(c, x, "cck")

    node(g, "d_corpus", "05_corpus.json", "{corpus:{sessions}}", "art")

    with g.subgraph(name="cluster_s6") as c:
        c.attr(**cattr("Stage 6 · 接地闸 grounding = ★边 B   ★命门3", "#E8F5E9", "#66BB6A"))
        node(c, "grd", "line.ground()?", "attributed(值, 锚, window=90)\n逐字 + 就近共现", "gate")
        node(c, "surv", "survival by_line/by_cap", "不接地即弃 + reason", "code")
        edge(c, "grd", "surv")

    node(g, "d_ground", "06_grounded_questions.json", "+ 06_grounding_report.json", "art")
    node(g, "ship", "出厂题库", "", "se")

    edge(g, "scen", "d_in"); edge(g, "d_in", "council"); edge(g, "polish", "d_wp")
    edge(g, "d_wp", "wbatch"); edge(g, "wprep", "d_world")
    edge(g, "d_world", "feas", "订单支", "branch")
    for x in ("l1", "l2", "l3", "l5"):
        edge(g, x, "d_orders")
    edge(g, "d_orders", "eaq"); edge(g, "eaq", "qint", "过闸的 order")
    edge(g, "qph", "d_q")
    edge(g, "d_world", "cfac", "语料支", "branch")
    edge(g, "cck", "d_corpus")
    edge(g, "d_q", "grd", "题面+答案"); edge(g, "d_corpus", "grd", "语料")
    edge(g, "surv", "d_ground"); edge(g, "d_ground", "ship")
    return g


# ════════════════════════════════════════════════════════════════════════════
# 图 2:闭环驱动 build_to_target 双环
# ════════════════════════════════════════════════════════════════════════════
def build_closedloop():
    g = newgraph("oursys · 闭环驱动 build_to_target(双环)")
    legend(g)
    node(g, "spec", "TargetSpec", "min_questions / per_line_min", "se")
    node(g, "inv", "invert_rate(反推)", "保守 survival + slack → WorldParams\nn_ent/n_sess/quota · 盈余摊进各线 floor", "loop")
    node(g, "patch", "patch 白皮书规模 + config.quotas", "", "code")
    edge(g, "spec", "inv"); edge(g, "inv", "patch")

    with g.subgraph(name="cluster_sup") as c:
        c.attr(**cattr("① 订单供给环(便宜:world+enumerate,绝不渲)", "#FFF3E0", "#FB8C00"))
        node(c, "sworld", "stage_world(LLM,含 CRITIC)", "", "llm")
        node(c, "sord", "stage_orders", "配额驱动 run_lines", "code")
        node(c, "ddef", "赤字? 实产 < 配额", "", "gate")
        node(c, "grow1", "_grow_for_supply", "长世界 n_ent/n_sess↑ 重建\n(≤ order_subrounds)", "loop")
        edge(c, "sworld", "sord"); edge(c, "sord", "ddef")
        edge(c, "ddef", "grow1", "有赤字", "fb"); edge(c, "grow1", "sworld", "", "fb")

    with g.subgraph(name="cluster_ren") as c:
        c.attr(**cattr("渲染 + 接地(贵)", "#ECEFF1", "#90A4AE"))
        node(c, "sq", "stage_questions(LLM)", "", "llm")
        node(c, "scor", "stage_corpus(LLM,整轮重渲)", "", "llm")
        node(c, "sgrd", "stage_grounding(边 B)", "", "code")
        edge(c, "sq", "scor"); edge(c, "scor", "sgrd")

    node(g, "floor", "floor 达标?", "逐线 grounded ≥ floor\n且 总数 ≥ min_questions", "gate")
    node(g, "met", "MET · 出厂题库", "", "se")
    node(g, "unmet", "UNMET · fail-open", "留痕 manifest.met_status", "se")
    node(g, "grow2", "② 实测纠偏环", "按【实测 survival】重 invert_rate\n+ 整轮重渲(≤ max_rounds)", "loop")

    edge(g, "patch", "sworld")
    edge(g, "ddef", "sq", "供给足", "ok")
    edge(g, "sgrd", "floor")
    edge(g, "floor", "met", "达标", "ok")
    edge(g, "floor", "grow2", "可行线未达 growable", "fb")
    edge(g, "floor", "unmet", "floor 落不可行线 permanent", "warn")
    edge(g, "grow2", "patch", "② 放大配额/世界 → 整轮重来", "fb")
    return g


# ════════════════════════════════════════════════════════════════════════════
# 图 3:验证三角(三位一体)+ 三命门
# ════════════════════════════════════════════════════════════════════════════
def build_triangle():
    g = newgraph("oursys · 验证三角(题面 / 答案 / 语料)+ 三命门")
    g.attr(ranksep="1.0", nodesep="1.2")
    node(g, "world", "唯一真相源:世界 WorldState", "★命门1 共享世界基质(防自相矛盾)", "se")
    node(g, "q", "题面 question", "phrase 出的自然语言", "art")
    node(g, "gold", "答案 gold", "各线代码 gt · ★命门2", "art")
    node(g, "c", "语料 corpus", "render 出的文档 · 只认世界", "art")

    edge(g, "world", "q", "出题意图", "branch")
    edge(g, "world", "gold", "enumerate / gt", "branch")
    edge(g, "world", "c", "render", "branch")

    g.edge("q", "gold", label="  边 A · 良定义 well_posed()\n答案是否此题唯一正确解\n〔设计完成 · 待落地〕  ",
           color="#7E57C2", penwidth="2.4", dir="both", fontname=F, fontsize="11", fontcolor="#5E35B1")
    g.edge("gold", "c", label="  边 B · 接地 grounding()\ngold 能否在语料逐字+就近找到\n〔已建 · ★命门3〕  ",
           color="#2E7D32", penwidth="2.4", dir="both", fontname=F, fontsize="11", fontcolor="#2E7D32")
    g.edge("q", "c", label="  边 C · 可答性 answerability\n只读语料能否答题(reader)\n〔future〕  ",
           color="#9A6700", penwidth="1.8", dir="both", style="dashed", constraint="false",
           fontname=F, fontsize="11", fontcolor="#9A6700")
    return g


if __name__ == "__main__":
    for name, builder in [("oursys_pipeline", build_pipeline),
                          ("oursys_closedloop", build_closedloop),
                          ("oursys_triangle", build_triangle)]:
        g = builder()
        for fmt in ("svg", "pdf"):
            g.format = fmt
            g.render(f"vis/{name}", cleanup=True)
        print(f"✓ {name}.svg / .pdf")
