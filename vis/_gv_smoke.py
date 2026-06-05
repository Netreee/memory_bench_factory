import os; os.environ["PATH"]="/opt/homebrew/bin:"+os.environ.get("PATH","")
import graphviz
F="PingFang SC"
g=graphviz.Digraph(format="png"); g.attr(rankdir="TB", fontname=F, bgcolor="white", nodesep="0.4", ranksep="0.6")
g.attr("node", fontname=F, fontsize="11")
g.attr("edge", fontname=F, fontsize="10")
with g.subgraph(name="cluster_s") as c:
    c.attr(label="Stage 2 · 共享世界  ★命门1", style="rounded,filled", fillcolor="#F4F7FB", color="#B0BEC5", fontsize="14", fontcolor="#263238")
    c.node("a", label='<<b>world.batch ×N</b><br/><font point-size="9" color="#6A4FB6">LLM 并发 · ≤4轮 · merge去重</font>>',
           shape="box", style="rounded,filled", fillcolor="#EDE7F6", color="#7E57C2", penwidth="1.6")
    c.node("b", label='<<b>assemble_world</b><br/><font point-size="9" color="#00796B">纯代码 · 状态机切片</font>>',
           shape="box", style="rounded,filled", fillcolor="#E0F2F1", color="#26A69A", penwidth="1.6")
    c.edge("a","b")
g.node("d", label='<<b>02_world.json</b>>', shape="note", style="filled", fillcolor="#FFF8E1", color="#FFB300", penwidth="1.4")
g.node("gate", label='<<b>⟐ feasible?</b><br/><font point-size="9">基质不足→跳</font>>', shape="box", style="rounded,filled", fillcolor="#FFE0B2", color="#FB8C00", penwidth="1.6")
g.edge("b","d", color="#455A64", penwidth="1.6"); g.edge("d","gate", color="#455A64", penwidth="1.6")
# 图例(HTML 表)
leg='''<<table border="1" cellborder="0" cellspacing="6" cellpadding="4" color="#B0BEC5">
<tr><td bgcolor="#EDE7F6"> LLM 调用 </td><td bgcolor="#E0F2F1"> 纯代码 </td><td bgcolor="#FFF8E1"> 产物json </td><td bgcolor="#FFE0B2"> 闸 </td></tr></table>>'''
g.node("legend", label=leg, shape="plaintext")
g.render("vis/_gv_smoke", cleanup=True)
print("ok")
