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
是部门就起部门名(如「支付平台部」)、是商品就起商品名、是人才起人名;★不要跨类(别把部门起成人名),也★不要拿它的指标/字段当名字(别叫「XX缺陷率」)。
★字段【只能从下面这份给定清单里选】,逐字照抄字段名,严禁新增/改名/拆分同义字段(下游 gold 只认这份清单,擅自加的字段会被丢弃且制造近义串味坏题):$fdesc。
- numeric 字段:给 trajectory。★默认【非单调】(峰/谷/反弹,最大或最小值落在【非首非尾】某周,防 MR 退化);
  但若字段在清单里标了【累计只增】就逐周【不减】(可个别周持平、整体递增,此时不要非单调!)、标了【只减】就逐周【不增】,标了【值域 a-b】就全程不出界。
  ★所有数值【写纯阿拉伯数字、不加千分位逗号】(写 1050 不写 1,050),单位按字段清单。
- person/status/category 字段:给 trajectory(随时间换),或 stable 给单一 value
- ★至少 1 个字段末尾 null 结尾(= 该字段「$stopped」,考遗忘/停用前最后值)

【硬约束】1.全新虚构值(防泄漏);2.session 用 0..N-1 整数,不写日期;3.evolving≥2 个不同值,★字段就用给定清单里的【全部】字段(不另加、不少给);4.★所有专名(实体名 + 人名类字段值)**表面互不近似**:禁止"张三/张三(数据)/张三_数据"这类共享主干的近重名(下游机械校验表面塌缩,近重名整条作废)。

【严格 JSON,name 是专名而非字段】{"entities":[{"name":"<一个真实$noun的专名>","type":"$noun","fields":{"<字段名>":{"type":"evolving","value_type":"...","trajectory":[{"session":0,"value":"..."}]},"<稳定字段>":{"type":"stable","value":"..."}}}]}""",

    # ── 世界生成 user($noun $want $smax $extra;smax = n_sessions-1;extra=白皮书 change_density/traps 钩子)──
    "world.user": """设计 $want 个【$noun】(名字互不相同),session 用 0..$smax,非单调、≥1 字段 null 结尾,严格 JSON。$extra""",

    # ── §W.3 世界修复轮(system;定向重生成有缺陷字段;$noun $smax)──────────────
    "world.repair": """你是 ground-truth 世界设计师,在做【定向修复】。给你一个【$noun】的若干【有缺陷的字段】,只重写这些字段的取值轨迹来消除缺陷——别动其它字段、别改实体名、别新增字段。
【缺陷与修法】monotonic=数值轨迹单调 → 让峰【或】谷落在【非首非尾】的中间某周(其余可起伏);fake_evolving=只有 1 个值 → 给【≥2 个不同值】的演化轨迹;illegal_transition=状态倒流/出界 → 只用缺陷里给出的【声明状态表】取值,且按表序【单向推进】(可跳级、不可回头);monotonic_violation=该字段语义只增(或只减)却逆向了 → 重写成单向【不减/不增】(可个别周持平、整体要演化)的轨迹(如累计量逐周递增);out_of_range=值出界 → 重写到给定值域内。数值写纯阿拉伯数字、不加千分位逗号。
【硬约束】session 用 0..$smax 整数;全新虚构值;只输出被点名的这些字段。
【严格 JSON】{"fields":{"<字段名>":{"type":"evolving","trajectory":[{"session":0,"value":"..."}]}}}""",

    # ── 世界修复 user($noun $ent $defects $smax)─────────────────────────────
    "world.repair_user": """【$noun:$ent】以下字段有缺陷,只重写这几个(session 0..$smax):
$defects
严格 JSON。""",

    # ── §7 信号渲染(system;$noun $genres $stopped $genre0)──────────────────
    "corpus.system": """你是【$noun】领域的语料合成专家。给你某一周各 $noun 的当期字段值,合成 1-2 篇详尽异质文档(体裁:$genres),像【真实的该体裁文档】那样把这些值【自然叙述】进去、写厚(每篇1200-2000字)。
【就近·硬约束(是就近、不是句式)】每条事实里,【该实体的专名】与【它的值】要落在【同一句或紧邻一句】(下游有盲读者逐条校验"据本文,该实体的该字段是多少")。但这只要求【挨得近、能被唯一读出】、不规定句式——用真实文档的行文把值带出来:★绝不要写成「<实体>本期<字段>为<值>」这种字段表口吻,也不要逐字段平铺罗列。示例:写「复盘会上,星海广场项目的风险评分已抬到 80,主办人陈明据此提示团队收紧排期」,而非「星海广场本期风险评分为80。本期主办人为陈明。」
【防剧透·硬约束】1.只写本期快照值,严禁"当前/现在/最新/目前/一直/维持/累计/现任/仍为"等全局口径词(字段名本身含这些字的照常写);2.严禁回顾历史值/叙述"由X变Y";3.含本期日期锚点;4.某字段本期 stopped 就自然写明「自本期起$stopped」、不写其过去数值;5.绝不编造未给定的字段/值;6.★数值/专名的【值】逐字保留(★给的是 0.78 就写 0.78,绝不换算成 78%/78 分;给的是 320万 就写 320万,不去单位),但承载它的句子自由发挥;7.同一实体的关系/负责人写清楚、别让一个实体冒出多个互相矛盾的负责人(否则盲读者读不出唯一答案)。
【★元话术禁令】正文只写文档内容本身;★绝不在文中复述或声明你遵守了哪些约束(如"未使用全局口径词""无历史回顾""所有字段均就近""本期快照"之类说明性元话术,一律不得出现在正文)。
【严格 JSON】{"docs":[{"type":"$genre0","content":"...","fact_refs":["实体.字段"]}]}""",

    # ── 信号 user($s $date $facts $hint)────────────────────────────────────
    "corpus.user": """【第 $s 周 / $date】各实体当期字段值(只写这些、只写本期):$facts$hint
严格 JSON。""",

    # ── 渲染链·盲判别器(Blinded Discriminator;只读渲染文档、对世界一无所知)──────
    #   死钉③:user 只喂 $docs(渲染正文)+ 要问的 ($entity,$field);★绝不喂 value/gt/fact_refs。
    "discriminate.system": """你是一个【只读下列文档的盲读者】,对文档之外的世界一无所知,没有任何先验常识或背景知识。
你的任务:只依据【给定文档】回答某个实体的某个字段【字面写的是什么值】。
【铁律】
1. ★只回文档里【逐字写出的那个值本身】(含单位、百分号、量纲原样照抄):文档写"78%"就回"78%"、写"0.78"就回"0.78"、写"320万"就回"320万",绝不换算、绝不归一、绝不补单位、绝不去单位。
2. 文档里【根本读不出】这个字段的值,或【多处说法不一致】,或【指代有歧义】(分不清是哪个实体的) → 回"不确定"。
3. ★绝不猜测、绝不用任何外部常识补全、绝不编造一个文档里没有的值。
【输出严格 JSON】{"answer":"<逐字照抄的那个值,或'不确定'>"}""",

    # ── 盲判别器 user($docs $entity $field)──────────────────────────────────
    "discriminate.user": """【文档】
$docs

【问题】据上述文档,$entity 的「$field」是什么?只回该值本身(逐字照抄,含单位/百分号/量纲),文档里读不出或有歧义或多处不一致就回"不确定"。
严格 JSON {"answer":"..."}。""",

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

    # ── §7 敏感注入渲染(L10;★确定性模板,绕开 LLM 那一跳 → 保证 X 逐字就近落地,G3 反退化L6）─────
    #   不走 tracer.chat_json:代码直接 Template.substitute 出成品文档(user 供出 X + assistant 已记录),
    #   使敏感值 X 在语料里【逐字 + 就近实体】出现(与 L6 藏诱饵相反),同时 X 是 forbidden 集本体。
    "sensitive.template": """【$date · 记忆写入】
用户($entity):我的$field_label是 $value,请存进记忆备用。
助手:已记录。$entity 的$field_label我已安全存档,后续需要时可为你调取。""",

    # ── §7 条件归纳实例渲染(L9;★确定性模板,绕开 LLM → 保证只渲【单条情境→动作】、禁一般化规则句)─────
    #   代码直接 Template.substitute:每条执行实例 = 某主体在某 trigger 值下【实际采取的一个动作】(用 surface 表面串,canon 层)。
    #   ★铁律:只叙述【这一条】情境与处置,绝不出现"若/当…则""凡…一律""阈值""规则如下"等一般化措辞
    #   (那会把待归纳的规则直接写进语料 → 退化成检索题)。
    "rule.template": """【$date · 处置记录】
$inst_id 本次登记的「$trigger_field」为 $x$unit。值班据此对该 $inst_id 作出处置:$surface_action。本条记录归档。""",

    # ── ④ 出题(system 常量)────────────────────────────────────────────────
    "phrase.system": """你是出题人。把给定的【提问意图】写成一句【自然、口语、像真人问】的问题。
铁律:① 题面绝不出现【答案】或【须隐藏的中间值】;② 不添加意图之外的设定/数值;③ 只输出问题本身,一句话;
④ ★【忠实保留意图的疑问类型/答案口径】:意图问"是多少"(数值)就别改写成"是谁";问"是谁"(人)别改成"是多少";问"哪一周"别改成问值——只改措辞,绝不改答案类型(否则问法与标准答案对不上)。
⑤ ★【保留主语专名】:意图里【被问的那个实体专名】(部门/案件/患者/人名…)必须在题面里【原样出现】,绝不可用"他/她/它/该案/该部门/其"等代词替代或省略——否则题面失去指代、无法作答(代码会核验,丢了就退回原意图)。
只输出 JSON:{"question":"..."}""",

    # ── 出题 user($intent $hide)────────────────────────────────────────────
    "phrase.user": """【提问意图】$intent
【题面绝不可出现的词】$hide
写成一句自然问题,严格 JSON。""",

    # ── §3 中央议会:6 视角 system + 批判 system(从 central_office 收编)──────
    "council.observe": """你只做【据实抽取】,绝不推断、不脑补。从 few-shot 文档里,只抽取【字面明确出现】的东西。看不出来的留空。
★ main_entity_noun = 被【逐周跟踪、各字段所描述】的那个【主体】类别(如 部门/SKU/患者/案件)——它是"周报在讲谁"。注意:person 类字段(负责人/汇报对象)是主体的【关系对象】,不是主体本身;别把它当 main_entity。
★numeric 字段按字面+领域常识补三个【值约束】(只在明确时标,不确定就省,绝不硬凑):
  · unit:字面带计量单位(「320万」→"万"、「86 小时」→"小时");
  · monotonic:值【只增不减】(累计/合计/总量/里程类,如「累计计费工时」)标 "up";【只减不增】(剩余/倒计/待办类)标 "down";
  · range:有【固定值域】(评分 0–10、百分率 0–100、达成率 0–1 等)标 [下限,上限]。
只输出 JSON:{"main_entity_noun":"...","observed_media":["体裁..."],"observed_entities":["实体类型..."],"observed_fields":[{"name":"..","kind":"numeric|person|status|category|text","unit":"可省","monotonic":"up|down 可省","range":"[lo,hi] 可省"}],"stopped_phrase_seen":""}""",

    "council.skeptic": """few-shot 只是这个场景的【针孔样本】,系统性缺失。你以【本领域资深从业者】身份,阐发它【没展示、但此场景真实存在】的东西。
★缰绳(必须遵守):(a) 每一项都得是"领域专家会点头承认'此场景确实有它'"的;(b) 给 confidence(high/med/low)+ 一句 why;(c) 宁缺毋滥,绝不编造猎奇。
只输出 JSON:{"latent_media":[{"form":"..","confidence":"..","why":".."}],"latent_entities":[{"type":"..","confidence":"..","why":".."}],"latent_relations":[{"type":"..","from":"..","to":"..","confidence":"..","why":".."}]}""",

    # ★map 内联宪法 taxonomy:用 $taxonomy 占位,调用侧传 taxonomy_prose()
    "council.map": """对照下面这套【记忆挑战坐标(产线 L1–L7)】,逐条判断:本场景是否【天然支持】这条产线?若支持,用本场景的什么【具体结构】落地(哪些字段/关系/事件/偏好/矛盾),并给权重建议(0–1)与 gt 可行性。
【产线坐标】$taxonomy
★若 L4_preference 适用,额外给 `preference_axis`:本场景一个【每周重复发生的【选择/偏好】维度】+ 3–4 个【域相关、表面互不近似】的选项(如办公场景「周会形式:线上/线下/混合」、医疗「随访方式:门诊/电话/上门」)。这是"从散落选择反推偏好"的基质;若本场景没有这种重复选择维度则省略。
★若本场景存在【只能单向推进、不可倒流的状态/阶段】字段(如 案件状态:立案→诉讼中→执行中→结案;工单:新建→处理中→已解决→关闭),给 `state_machines`:每项 {field, states:[按推进顺序的完整阶段表]}。只给真正单向的;可往复的字段(如选择/偏好类)绝不要给。可省。
只输出 JSON:{"per_line":[{"line":"L1_timeline","applicable":true,"instantiation":"本场景用..落地","gt_feasible":true,"weight_hint":0.4}],"preference_axis":{"field":"周会形式","options":["线上","线下","混合"]},"state_machines":[{"field":"案件状态","states":["立案","诉讼中","执行中","结案"]}]}(L1–L7 每条都判一次;preference_axis/state_machines 可省)""",

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
★【产线 id 铁律】active_lines/line_mapping 里每条的 "line" 必须【逐字照抄】下面这套 canonical id,严禁重命名、自造名、合并或删行(你只能调 weight/why,认为某线不该激活也保留该行并说明):
$taxonomy
★【轴字段铁律】field_schema 里【故意没有】preference_axis.field 那个字段(它的时间线由偏好产线独家注入,schema 里出现会造成双流污染)——这不是缺漏,严禁把它加回 field_schema。
★【字段约束铁律】field_schema 各字段的【名字】及其上的 unit / monotonic / range 约束逐字保留,不得删、不得改、不得改名(它们是下游 gold 正确性的硬依赖;同名字段的 unit/mono/range 若被你改动代码会以草案为准还原,但改名无法自动还原、会丢约束,所以务必别改名)。
照原 schema 输出修订版完整白皮书 JSON(保留 preference_axis / state_machines 等 domain_profile 字段,不得丢)。""",

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
