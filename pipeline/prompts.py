"""
pipeline.prompts —— 所有 LLM prompt 的【单一注册表 + 渲染层】。

工程目标:把散落在 central_office.py / run_factory_v2.py 里的 prompt 字符串常量 + inline f-string 收口到一处,
代码侧只调 render("<stage>.<role>", **vars),不再夹带 prompt 文本。

约定:
- 占位用 $var(string.Template)。★prompt 里大量的 JSON `{}` 保持【字面】,不与占位冲突(避开 .format 的转义地狱)。
- 模板里【只放文本】;表达式(n_sessions-1 / json.dumps() / sorted(blocked)[:30] 等)在【调用侧】算好再传进来。
- 键名 = "<阶段>.<角色>",如 world.system / corpus.user / phrase.system。

本文件先收编【run_factory_v2 渲染侧】prompts;议会(central_office)prompts 待其稳定后并入。
"""
from string import Template

PROMPTS: dict[str, str] = {

    # ── §5 世界生成(system;域参数化:$noun $fdesc $stopped）──────────────
    "world.system": """你是 memory benchmark 的 ground-truth 世界设计师。为【$noun】设计一批随时间(周)演化的真值表。\
代码会据此机械算标准答案,你只填表——★严禁写问题/答案,严禁"最新/当前/截至…仍为"这类结论性措辞。

【每个 $noun = 一个 entity】★核心:name 是这个【$noun】个体的【正式专名】,且必须【与「$noun」这个类别相称】——\
是部门就起部门名(如「支付平台部」)、是商品就起商品名、是人才起人名;★不要跨类(别把部门起成人名),也★不要拿它的指标/字段当名字(别叫「XX缺陷率」)。字段才是它的属性与指标:$fdesc。
- numeric 字段:给 trajectory,★轨迹必须【非单调】(峰/谷/反弹),最大或最小值落在【非首非尾】某周
- person/status/category 字段:给 trajectory(随时间换),或 stable 给单一 value
- ★至少 1 个字段末尾 null 结尾(= 该字段「$stopped」,考遗忘/停用前最后值)

【硬约束】1.全新虚构值(防泄漏);2.session 用 0..N-1 整数,不写日期;3.evolving≥2 个不同值,每实体 4-6 字段;4.★所有专名(实体名 + 人名类字段值)**表面互不近似**:禁止"张三/张三(数据)/张三_数据"这类共享主干的近重名(下游机械校验表面塌缩,近重名整条作废)。

【严格 JSON,name 是专名而非字段】{"entities":[{"name":"<一个真实$noun的专名>","type":"$noun","fields":{"<字段名>":{"type":"evolving","value_type":"...","trajectory":[{"session":0,"value":"..."}]},"<稳定字段>":{"type":"stable","value":"..."}}}]}""",

    # ── 世界生成 user($noun $want $smax $extra;smax = n_sessions-1;extra=白皮书 change_density/traps 钩子)──
    "world.user": """设计 $want 个【$noun】(名字互不相同),session 用 0..$smax,非单调、≥1 字段 null 结尾,严格 JSON。$extra""",

    # ── §W.3 世界修复轮(system;定向重生成有缺陷字段;$noun $smax)──────────────
    "world.repair": """你是 ground-truth 世界设计师,在做【定向修复】。给你一个【$noun】的若干【有缺陷的字段】,只重写这些字段的取值轨迹来消除缺陷——别动其它字段、别改实体名、别新增字段。
【缺陷与修法】monotonic=数值轨迹单调 → 让峰【或】谷落在【非首非尾】的中间某周(其余可起伏);fake_evolving=只有 1 个值 → 给【≥2 个不同值】的演化轨迹。
【硬约束】session 用 0..$smax 整数;全新虚构值;只输出被点名的这些字段。
【严格 JSON】{"fields":{"<字段名>":{"type":"evolving","trajectory":[{"session":0,"value":"..."}]}}}""",

    # ── 世界修复 user($noun $ent $defects $smax)─────────────────────────────
    "world.repair_user": """【$noun:$ent】以下字段有缺陷,只重写这几个(session 0..$smax):
$defects
严格 JSON。""",

    # ── §7 信号渲染(system;$noun $genres $stopped $genre0)──────────────────
    "corpus.system": """你是【$noun】领域的语料合成专家。给你某一周各 $noun 的当期字段值,合成 1-2 篇详尽异质文档(体裁:$genres),把值自然叙述进去、写厚(每篇1200-2000字)。
【★就近归属·第一硬约束】每条事实必须把【该实体的专名】与【该字段的值】写在【同一句或紧邻一句】内(形如「<实体专名>本期<字段>为<值>」),严禁实体名只在标题/段首出现、把值散落到后文段落——下游会逐条机械校验"实体名与值就近共现(±90字)",相隔过远即判该事实没写到、整篇作废重渲。宁可一句话点清(名+字段+值),也别拆开写漂亮。
【防剧透硬约束】1.只写本期快照值,严禁"当前/现在/最新/目前/一直/维持/累计/现任/仍为"等全局口径词;2.严禁回顾历史值/叙述"由X变Y";3.含本期日期锚点;4.某字段本期 stopped 就自然写明「自本期起$stopped」(不写其过去数值);5.信息可分散到多篇,但【每条事实的名+值仍须就近】;6.绝不编造未给定的字段/值。
【严格 JSON】{"docs":[{"type":"$genre0","content":"...","fact_refs":["实体.字段"]}]}""",

    # ── 信号 user($s $date $facts $hint)────────────────────────────────────
    "corpus.user": """【第 $s 周 / $date】各实体当期字段值(只写这些、只写本期):$facts$hint
严格 JSON。""",

    # ── §7 草堆渲染(system;$noun)─────────────────────────────────────────
    "filler.system": """你生成【$noun】领域团队的杂项干扰文档,给记忆评测语料制造"草堆"。题材任选且每篇不同(培训/通知/公告/政策/会议/系统维护…)。
【硬约束】1.★绝不碰任何被追踪的 $noun 名/人名/字段(连名字都不出现,不给数值/状态),只写完全无关的日常杂事;2.自然像真文档,每篇600-1000字,带本周日期。
【严格 JSON】{"docs":[{"type":"通知","content":"..."}]}""",

    # ── 草堆 user($s $date $want $blocked)──────────────────────────────────
    "filler.user": """【第 $s 周 / $date】生成 $want 篇互不相同的干扰文档。★严禁出现:$blocked
严格 JSON。""",

    # ── §7 冲突渲染(L5;低可信"小道"来源文档,陈述被矛盾的值)──────────────────
    "conflict.system": """你为记忆评测语料合成一篇【低可信来源】文档:它以【小道消息/未经核实】的口吻,声称某事实是某个值。
【硬约束】1.★必须让读者一眼看出这是【未经证实、来源不可靠】的传闻(用"据传/有未经核实的说法/小道消息称/暂未官方确认/有待核实"等口吻),绝不写成正式通报;2.只围绕给定的【实体·字段·值】展开,把那个值自然说进去,不另编其它被追踪的数值;3.含本期日期锚点(传闻就该说"据传现在/目前…",不必回避这类措辞——这正是"当期有人在传"的冲突设定);4.每篇 500-900 字。
【严格 JSON】{"docs":[{"type":"传闻","content":"..."}]}""",

    # ── 冲突 user($s $date $entity $field $value $source)────────────────────
    "conflict.user": """【第 $s 周 / $date】请写一篇【$source】口吻的低可信文档:声称【$entity】的「$field」是「$value」(强调这只是未经证实的说法、尚无官方确认)。
严格 JSON。""",

    # ── ④ 出题(system 常量)────────────────────────────────────────────────
    "phrase.system": """你是出题人。把给定的【提问意图】写成一句【自然、口语、像真人问】的问题。
铁律:① 题面绝不出现【答案】或【须隐藏的中间值】;② 不添加意图之外的设定/数值;③ 只输出问题本身,一句话;
④ ★【忠实保留意图的疑问类型/答案口径】:意图问"是多少"(数值)就别改写成"是谁";问"是谁"(人)别改成"是多少";问"哪一周"别改成问值——只改措辞,绝不改答案类型(否则问法与标准答案对不上)。
只输出 JSON:{"question":"..."}""",

    # ── 出题 user($intent $hide)────────────────────────────────────────────
    "phrase.user": """【提问意图】$intent
【题面绝不可出现的词】$hide
写成一句自然问题,严格 JSON。""",

    # ── §3 中央议会:6 视角 system + 批判 system(从 central_office 收编)──────
    "council.observe": """你只做【据实抽取】,绝不推断、不脑补。从 few-shot 文档里,只抽取【字面明确出现】的东西。看不出来的留空。
★ main_entity_noun = 被【逐周跟踪、各字段所描述】的那个【主体】类别(如 部门/SKU/患者/案件)——它是"周报在讲谁"。注意:person 类字段(负责人/汇报对象)是主体的【关系对象】,不是主体本身;别把它当 main_entity。
只输出 JSON:{"main_entity_noun":"...","observed_media":["体裁..."],"observed_entities":["实体类型..."],"observed_fields":[{"name":"..","kind":"numeric|person|status|category|text"}],"stopped_phrase_seen":""}""",

    "council.skeptic": """few-shot 只是这个场景的【针孔样本】,系统性缺失。你以【本领域资深从业者】身份,阐发它【没展示、但此场景真实存在】的东西。
★缰绳(必须遵守):(a) 每一项都得是"领域专家会点头承认'此场景确实有它'"的;(b) 给 confidence(high/med/low)+ 一句 why;(c) 宁缺毋滥,绝不编造猎奇。
只输出 JSON:{"latent_media":[{"form":"..","confidence":"..","why":".."}],"latent_entities":[{"type":"..","confidence":"..","why":".."}],"latent_relations":[{"type":"..","from":"..","to":"..","confidence":"..","why":".."}]}""",

    # ★map 内联宪法 taxonomy:用 $taxonomy 占位,调用侧传 taxonomy_prose()
    "council.map": """对照下面这套【记忆挑战坐标(产线 L1–L7)】,逐条判断:本场景是否【天然支持】这条产线?若支持,用本场景的什么【具体结构】落地(哪些字段/关系/事件/偏好/矛盾),并给权重建议(0–1)与 gt 可行性。
【产线坐标】$taxonomy
只输出 JSON:{"per_line":[{"line":"L1_timeline","applicable":true,"instantiation":"本场景用..落地","gt_feasible":true,"weight_hint":0.4}]}(L1–L7 每条都判一次)""",

    "council.medium": """发散本场景【所有可能的文档/记录形式】。先穷尽【常见】形式(力求大而全),再补【反常但合理】的(人类一下子想不到、但此领域确实可能存在的)——每个反常项必须给"为何此场景合理"。不是猎奇,是覆盖完整。最后给主媒介组合建议。
只输出 JSON:{"common_media":[".."],"unconventional_media":[{"form":"..","why_plausible":".."}],"recommended_mix":["..",".."]}""",

    "council.style": """从 few-shot 原文抽【风格 DNA】,供后续渲染【照着仿写】:语气、格式(连续段落?条目?表格?)、典型篇幅、术语/黑话密度、什么明说·什么默认。
只输出 JSON:{"style_spec":{"tone":"..","format":"..","length":"..","jargon":"..","stated_vs_assumed":".."},"use_fewshot_as_exemplar":true}""",

    "council.traps": """设计本场景【天然在哪坑记忆系统】,让 benchmark 有区分度:recency 偏置 / 长程依赖 / 多源矛盾 / 易混字段(语义近但不同名) …。★不要用"近重名实体"(同主干近重名如 张三/张三(数据))——它在渲染层会塌缩成歧义、制造无唯一解的坏题,已禁用。每个陷阱说明它考验哪种记忆失败,并建议该【重激活哪条产线】来制造它。
只输出 JSON:{"traps":[{"trap":"..","stresses":"..","boost_line":"L?_.."}]}""",

    "council.critic": """你是中央办公室【批判员】。审一份白皮书,挑硬伤并直接产出【修订后的完整白皮书 JSON】(不是 diff,照原 schema):
1. 域解析贴合 few-shot 吗?inferred 的有没有不合理(违反怀疑缰绳)?
2. active_lines 是否真贴合本场景天然结构、是否制造了区分度(别什么场景都只配 L1)?gt 都可行吗?
3. shared_world_spec 的实体/关系/周数撑得起激活的产线 + 目标题量吗?关系够 L2 用吗?
4. medium 选得对吗?style_spec 能指导渲染吗?
照原 schema 输出修订版完整白皮书 JSON。""",

    # ── 议会 user 模板:6 视角共用($desc $fs $ask)+ 批判($desc $fs $draft)──
    "council.view_user": """【场景描述】$desc
【few-shot 文档】$fs

$ask 严格 JSON。""",

    "council.critic_user": """【场景描述】$desc
【few-shot 文档】$fs

【代码装配的白皮书草案】$draft
挑硬伤,产出修订版完整白皮书 JSON(保留 active_lines/domain_profile/shared_world_spec 等全部字段)。""",
}


def render(name: str, **vars) -> str:
    """渲染一个 prompt。$var 占位;JSON `{}` 保持字面。缺失的 $var 原样保留(safe_substitute)。"""
    tmpl = PROMPTS.get(name)
    if tmpl is None:
        raise KeyError(f"未知 prompt: {name!r};可用:{sorted(PROMPTS)}")
    return Template(tmpl).safe_substitute(**vars)
