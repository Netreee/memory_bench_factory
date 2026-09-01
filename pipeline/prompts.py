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
    "world.system": """你是 memory benchmark 的 ground-truth 世界设计师。只为蓝图实体类型【$type_id / $noun】设计一批随时间($time_unit)演化的真值表。\
代码会据此机械算标准答案,你只填表——★严禁写问题/答案,严禁"最新/当前/截至…仍为"这类结论性措辞。

【每个 $noun = 一个 entity】★核心:name 是这个【$noun】个体的【正式专名】,且必须【与「$noun」这个类别相称】——\
是部门就起部门名(如「支付平台部」)、是商品就起商品名、是人才起人名;★不要跨类(别把部门起成人名),也★不要拿它的指标/字段当名字(别叫「XX缺陷率」)。
★字段【只能从下面这份给定清单里选】,逐字照抄字段名,严禁新增/改名/拆分同义字段(下游 gold 只认这份清单,擅自加的字段会被丢弃且制造近义串味坏题):$fdesc。
- numeric 字段:给 trajectory。★默认【非单调】(峰/谷/反弹,最大或最小值落在【非首非尾】某周,防 MR 退化);
  但若字段在清单里标了【累计只增】就逐周【不减】(可个别周持平、整体递增,此时不要非单调!)、标了【只减】就逐周【不增】,标了【值域 a-b】就全程不出界。
  ★所有数值【写纯阿拉伯数字、不加千分位逗号】(写 1050 不写 1,050),单位按字段清单。
- person/status/category 字段:给 trajectory(随时间换),或 stable 给单一 value；但 user 若标为【domain event 驱动】，这里只能给可选初态，后续变化留给 event effect
- ★至少 1 个字段末尾 null 结尾(= 该字段「$stopped」,考遗忘/停用前最后值)

【硬约束】1.全新虚构值(防泄漏);2.session 用 0..N-1 整数,不写日期;3.evolving≥2 个不同值,★字段就用给定清单里的全部【非结构驱动】字段(不另加、不少给；关系/event 驱动字段遵从 user 的单独说明);4.★所有专名(实体名 + 人名类字段值)**表面互不近似**:禁止"张三/张三(数据)/张三_数据"这类共享主干的近重名(下游机械校验表面塌缩,近重名整条作废)。

若给定字段清单是“无内在字段”,就输出空 fields,不要发明字段；关系字段会由结构编译器另行写入。

【严格 JSON,name 是专名而非字段,type 必须逐字为 type id】{"entities":[{"name":"<一个真实$noun的专名>","type":"$type_id","fields":{"<字段名>":{"type":"evolving","value_type":"...","trajectory":[{"session":0,"value":"..."}]},"<稳定字段>":{"type":"stable","value":"..."}}}]}""",

    # ── 世界生成 user($noun $want $smax $extra;smax = n_sessions-1;extra=白皮书 change_density/traps 钩子)──
    "world.user": """设计 $want 个【$noun】(type id=$type_id,名字互不相同),session 用 0..$smax；字段允许时做非单调演化并让至少一个字段 null 结尾。严格 JSON。$extra""",

    # ── 世界骨架实例化：只连已生成实体，不再发明实体/字段/关系/事件类型 ──
    "world.structure": """你是世界蓝图实例化器。给定【已经生成的 typed entities】和【机器契约 blueprint】，只实例化契约声明的关系与领域事件。
【硬约束】
1. 只能引用给定实体专名；relation 的 from/to 类型必须匹配声明，且每种至少达到 min_count；每个 relation id 唯一，禁止重复边；temporal=false 的静态关系 session 必须为 0。代码会根据 relation.field 在哪一端声明，将另一端专名写入该 owner 的 Timeline。对同一 owner.field，按 session 递增时必须是真实引用变化，禁止连续重复同一目标凑数量。
2. event 的 participants 必须逐角色匹配声明类型；每种至少达到 min_count；session 为 0..$smax。event id 唯一，且同一 type/session/完整 participants 只能出现一次，禁止只换 id 重复计数。
3. 每个 event effect 只能使用该事件 effect_fields 声明的 role/field；输出时 entity 必须等于 participants 中该 role 对应实体，并给 set 值。全局每个 (entity,field,session) effect slot 只能被一个事件使用；set 必须相对该字段此前值造成真实变化（status 换状态、numeric 遵守 monotonic 且数值不同），禁止冲突或空操作。
4. causal_rules 声明 A→B 时，至少造一对真实 A/B 事件；B 写 caused_by=A 的事件 id，session 差严格等于 delay_sessions。
5. typed entities 目录可能给出 event-owned 字段的 initial_state；effect 必须写成不同于其当时状态的真实变化，不能空操作。
6. 严禁输出 cascades；代码会从验证过的事件因果机械编译。严禁新增类型、字段或实体。
【严格 JSON】{"relations":[{"id":"rel-1","type":"<relation id>","from":"<实体>","to":"<实体>","session":0}],"events":[{"id":"evt-1","type":"<event id>","session":1,"participants":{"<role>":"<实体>"},"effects":[{"entity":"<该 role 实体>","field":"<声明字段>","set":"<新值>"}],"caused_by":"<可省,父事件id>"}]}""",

    "world.structure_user": """【时间制度】unit=$time_unit,cadence=$cadence,session=0..$smax
【world_blueprint】$blueprint
【typed entities】$entities
实例化全部 min_count 约束，严格 JSON。""",

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
    "corpus.system": """你是【$noun】领域的语料合成专家。场景里有多类对象：$type_legend。给你某一$time_unit各 typed entity 的当期字段值与领域事件,合成 1-2 篇详尽异质文档(体裁:$genres),像【真实的该体裁文档】那样把这些值与事件【自然叙述】进去、写厚(每篇1200-2000字)。
★每条 fact 的 entity_type 决定它是什么对象；严禁把 Boss/装备/项目/预算等一律称作 primary 类型。若给了 domain_events，文档应以“发生了什么”组织叙事，并忠实承载其 participants/effects；每个 event 的人类可读 label 必须逐字出现，并与全部 participant 专名写在同一篇文档里，而非退回字段清单。
【就近·硬约束(是就近、不是句式)】每条事实里,【该实体的专名】与【它的值】要落在【同一句或紧邻一句】(下游有盲读者逐条校验"据本文,该实体的该字段是多少")。但这只要求【挨得近、能被唯一读出】、不规定句式——用真实文档的行文把值带出来:★绝不要写成「<实体>本期<字段>为<值>」这种字段表口吻,也不要逐字段平铺罗列。示例:写「复盘会上,星海广场项目的风险评分已抬到 80,主办人陈明据此提示团队收紧排期」,而非「星海广场本期风险评分为80。本期主办人为陈明。」
【防剧透·硬约束】1.只写本期快照值,严禁"当前/现在/最新/目前/一直/维持/累计/现任/仍为"等全局口径词(字段名本身含这些字的照常写);2.严禁回顾历史值/叙述"由X变Y";3.含本期日期锚点;4.某字段本期 stopped 就自然写明「自本期起$stopped」、不写其过去数值;5.绝不编造未给定的字段/值;6.★数值/专名的【值】逐字保留(★给的是 0.78 就写 0.78,绝不换算成 78%/78 分;给的是 320万 就写 320万,不去单位),但承载它的句子自由发挥;7.同一实体的关系/负责人写清楚、别让一个实体冒出多个互相矛盾的负责人(否则盲读者读不出唯一答案)。
【★元话术禁令】正文只写文档内容本身;★绝不在文中复述或声明你遵守了哪些约束(如"未使用全局口径词""无历史回顾""所有字段均就近""本期快照"之类说明性元话术,一律不得出现在正文)。
【严格 JSON】{"docs":[{"type":"$genre0","content":"...","fact_refs":["实体.字段"]}]}""",

    # ── 信号 user($s $date $facts $hint)────────────────────────────────────
    "corpus.user": """【第 $s $time_unit / $date】各 typed entity 当期字段值(只写这些、只写本期):$facts
【本期 domain_events】$events$hint
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
    "filler.user": """【第 $s $time_unit / $date】生成 $want 篇互不相同的干扰文档。★严禁出现:$blocked
严格 JSON。""",

    # ── §7 冲突渲染(L5;低可信"小道"来源文档,陈述被矛盾的值)──────────────────
    "conflict.system": """你为记忆评测语料合成一篇【低可信来源】文档:它以【小道消息/未经核实】的口吻,声称某事实是某个值。
【硬约束】1.★必须让读者一眼看出这是【未经证实、来源不可靠】的传闻(用"据传/有未经核实的说法/小道消息称/暂未官方确认/有待核实"等口吻),绝不写成正式通报;2.只围绕给定的【实体·字段·值】展开,把那个值自然说进去,不另编其它被追踪的数值;3.含本期日期锚点(传闻就该说"据传现在/目前…",不必回避这类措辞——这正是"当期有人在传"的冲突设定);4.每篇 500-900 字。
【严格 JSON】{"docs":[{"type":"传闻","content":"..."}]}""",

    # ── 冲突 user($s $date $entity $field $value $source)────────────────────
    "conflict.user": """【第 $s $time_unit / $date】请写一篇【$source】口吻的低可信文档:声称【$entity】的「$field」是「$value」(强调这只是未经证实的说法、尚无官方确认)。
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
    "council.observe": """你只做【据实抽取】,绝不推断、不脑补。从 few-shot 文档里,只抽取【字面明确出现】的东西。看不出来的可选键直接省略，禁止输出“可省/无/未知/不适用”等占位值。
★ main_entity_noun = 被【逐周跟踪、各字段所描述】的那个【主体】类别(如 部门/SKU/患者/案件)——它是"周报在讲谁"。注意:person 类字段(负责人/汇报对象)是主体的【关系对象】,不是主体本身;别把它当 main_entity。
★numeric 字段按字面+领域常识补三个【值约束】(只在明确时标,不确定就省,绝不硬凑):
  · unit:字面带计量单位(「320万」→"万"、「86 小时」→"小时");
  · monotonic:值【只增不减】(累计/合计/总量/里程类,如「累计计费工时」)标 "up";【只减不增】(剩余/倒计/待办类)标 "down";
  · range:有【固定值域】(评分 0–10、百分率 0–100、达成率 0–1 等)标 [下限,上限]。
只输出 JSON:{"main_entity_noun":"...","observed_media":["体裁..."],"observed_entities":["实体类型..."],"observed_fields":[{"name":"..","kind":"numeric|person|status|category|text"}]}。unit/monotonic/range/stopped_phrase_seen 均为可选键，只在明确时添加；range 若有必须为两个 JSON 数字。""",

    "council.skeptic": """few-shot 只是这个场景的【针孔样本】,系统性缺失。你以【本领域资深从业者】身份,阐发它【没展示、但此场景真实存在】的东西。
★缰绳(必须遵守):(a) 每一项都得是"领域专家会点头承认'此场景确实有它'"的;(b) 给 confidence(high/med/low)+ 一句 why;(c) 宁缺毋滥,绝不编造猎奇。
只输出 JSON:{"latent_media":[{"form":"..","confidence":"..","why":".."}],"latent_entities":[{"type":"..","confidence":"..","why":".."}],"latent_relations":[{"type":"..","from":"..","to":"..","confidence":"..","why":".."}]}""",

    # ★map 内联宪法 taxonomy:用 $taxonomy 占位,调用侧传 taxonomy_prose()
    "council.map": """对照下面这套【记忆挑战坐标(产线 L1–L7)】,逐条判断:本场景是否【天然支持】这条产线?若支持,用本场景的什么【具体结构】落地(哪些字段/关系/事件/偏好/矛盾),并给权重建议(0–1)与 gt 可行性。
【产线坐标】$taxonomy
★若 L4_preference 适用,额外给 `preference_axis`，但它必须引用已冻结 world_blueprint 中某个 entity type 已声明的真实重复选择字段：{entity_type,field,options}。不得新增字段、不得要求 L4 事后造时间线；自然轨迹不够形成稳定偏好就判不适用。
★生命周期状态序已经冻结在 world_blueprint.fields[].states；这里只能引用它判断能力是否适用，严禁另写一套 state_machines 覆盖世界。
只输出 JSON:{"per_line":[{"line":"L1_timeline","applicable":true,"instantiation":"本场景用..落地","gt_feasible":true,"weight_hint":0.4}],"preference_axis":{"entity_type":"...","field":"...","options":["...","..."]}}(L1–L7 每条都判一次;preference_axis 可省)""",

    "council.medium": """发散本场景【所有可能的文档/记录形式】。先穷尽【常见】形式(力求大而全),再补【反常但合理】的(人类一下子想不到、但此领域确实可能存在的)——每个反常项必须给"为何此场景合理"。不是猎奇,是覆盖完整。最后给主媒介组合建议。
只输出 JSON:{"common_media":[".."],"unconventional_media":[{"form":"..","why_plausible":".."}],"recommended_mix":["..",".."]}""",

    "council.style": """从 few-shot 原文抽【风格 DNA】,供后续渲染【照着仿写】:语气、格式(连续段落?条目?表格?)、典型篇幅、术语/黑话密度、什么明说·什么默认。
只输出 JSON:{"style_spec":{"tone":"..","format":"..","length":"..","jargon":"..","stated_vs_assumed":".."},"use_fewshot_as_exemplar":true}""",

    "council.traps": """设计本场景【天然在哪坑记忆系统】,让 benchmark 有区分度:recency 偏置 / 长程依赖 / 多源矛盾 / 易混字段(语义近但不同名) …。★不要用"近重名实体"(同主干近重名如 张三/张三(数据))——它在渲染层会塌缩成歧义、制造无唯一解的坏题,已禁用。每个陷阱说明它考验哪种记忆失败,并建议该【重激活哪条产线】来制造它。
只输出 JSON:{"traps":[{"trap":"..","stresses":"..","boost_line":"L?_.."}]}""",

    "council.world": """你是【领域世界架构师】。你的任务不是列几个字段，而是先回答：这个场景的世界在骨子里由哪些不同类型的对象、关系和领域事件构成，它按什么时间制度变化？
从场景描述与 few-shot 出发，用领域常识补足针孔样本看不到但真实、必要的结构；不要按记忆题型反推世界，也不要把所有对象压成同一种 entity。
observe.observed_fields 是字面硬事实：其中每个 name 必须逐字出现在至少一个合理 entity type 上，observe 明示的 kind/unit/monotonic/range 也必须原样复制；它们可以只是外围观测字段，不能取代领域核心拓扑与事件。
★observe 的 person/category/text 是 few-shot 中的【字面显示值】，必须按原 kind 保留；它不等于 typed relation。若同一语义还需要可遍历关系，另加一个名称不同的 reference 字段承载关系，绝不能把已观测字段改成 reference，也不能复用非 reference 字段充当 relation.field。
★先逐项核对场景描述里的核心循环：凡是有独立身份、会参与关系/事件、需要被持续追踪的核心名词，都必须成为 entity type，不能降成 category/text 来省事；描述明确列出的核心关系与核心事件必须分别有 relation/event 声明。例如“阵营控制地区”必须有 faction 与 region 两端，不能偷换成“玩家控制地区”；若描述分别列出“掉落、拾取、装备”，就不能合并成一个含糊事件。

输出一个可执行 world_blueprint v1：
- entity_types：每类有稳定英文 id、中文 noun、count、唯一一个 primary=true、该类型【专属】fields。字段沿用 {name,kind,unit?,monotonic?,range?,states?}；关系字段也必须先声明，kind 用 reference。
- relation_types：{id,from_type,to_type,field,temporal,min_count}。from→to 必须能按“from 谓词 to”读成领域自然语义，绝不能因 field 在另一端就反转或替换端点；field 必须逐字声明在两个端点类型中的【恰好一端】，该端就是软外键 owner，值指向另一端。自关系约定 from/source 持有字段。v1 每个 reference 字段必须且只能绑定一种 relation，不能悬空，也不能同时被 event effect 写入。每种 relation 的 min_count>=1。一对多通常把 field 放在“多”的一侧，多对多改用关联实体。
- event_types：{id,label,roles:{角色:type_id},effect_fields:[{role,field}],min_count}。label 是文档可自然逐字使用的人类可读事件名（如“击败首领”“预算修订”）；每个事件至少一个真实状态效果且 min_count>=1。描述中分开的领域动作不得为了省 schema 被合并。
- temporal_model：{unit,cadence,n_sessions,step_days}。unit 可为 week/chapter/business_day/round/event 等；n_sessions>=2，step_days>=1。
- causal_rules：只有领域中无需额外主体选择、稳定必然成立的事件因果才写 {id,trigger_event,effect_event,delay_sessions}，没有就空数组；中间有人类/玩家选择时必须拆开，不能把“击败→掉落”偷换成“击败→拾取”。
- evidence_channels：这个世界里真实留下痕迹、可观察核心事件的文档/记录渠道。

★v1 不接受不可执行的 prose invariants。约束必须落到 fields.states/range/monotonic、relation_types、event_types.effect_fields 或 causal_rules 中；无法机械表达的只作为评审意见，不得伪装成已执行契约。
★可选字段不适用时直接省略（或为 JSON null），绝不拿 [0,0]、[null,null] 等占位。range 仅用于 numeric 且必须是两个不同的 JSON 数字；reference 的目标类型只由 relation_types 表达。monotonic 仅可写 up/down。只有一个 entity type 的 primary=true，其余必须 false。

★所有 id 唯一、引用闭合；跨类型同名字段若存在，kind/unit/monotonic/range 必须完全一致。至少设计 2 种实体类型、1 种关系和 1 种事件，形成该场景自己的拓扑与事件生态。不要提 L1–L10。
只输出 JSON:{"world_blueprint":{"version":1,"entity_types":[{"id":"...","noun":"...","count":3,"primary":true,"fields":[{"name":"...","kind":"status"}]}],"relation_types":[{"id":"...","from_type":"...","to_type":"...","field":"...","temporal":true,"min_count":1}],"event_types":[{"id":"...","label":"人类可读事件名","roles":{"actor":"..."},"effect_fields":[{"role":"actor","field":"..."}],"min_count":1}],"temporal_model":{"unit":"week","cadence":"weekly","n_sessions":10,"step_days":7},"causal_rules":[],"evidence_channels":["..."]}}""",

    "council.world_review": """你是【世界骨架反方评审】。你只审领域世界，不看也不迎合任何记忆产线。对候选 world_blueprint 做一次换皮压力测试并直接给出修订后的完整 blueprint：
1. 本体：实体类型是否真有不同身份/字段归属/生命周期，还是一个主表拆成几张同义表？
2. 动力学：领域事件是否真驱动状态迁移，effect 是否落在正确 owner；因果只保留领域稳定成立、无需额外主体选择就必然发生的链。尤其区分“击败导致掉落”与“玩家随后拾取”，前者不能直接写成击败必然导致拾取。
3. 拓扑：关系方向、时变性和关联实体是否符合领域，不能靠换 noun 就迁移到任意场景。逐条把 relation 读成“from 谓词 to”；field owner 在哪一端与语义方向无关，禁止因缺类型而用别的实体顶替（如玩家顶替阵营）。
4. 时间：unit/cadence 是否是这个世界自然运转的节律，而不是一律 weekly。
5. 证据生态：evidence_channels 是否是该领域真实留下痕迹的渠道，且足以观察核心事件。
6. 核心覆盖与换皮反证：先把场景描述中的核心名词、关系谓词、事件动词逐项对照 blueprint；任何一项被降成 category/text、偷换端点、合并事件或 min_count=0 都不得评 low。随后假设把所有 noun/field/id 换成另一个行业词，如果结构和动力学仍毫无违和，说明仍同质化，必须补领域独有结构；但不许靠无意义加类型凑差异。
保留 v1 schema，至少 2 个 entity type、1 个 relation、1 个 event、1 个 evidence channel；所有引用闭合、恰一个 primary。所有可执行约束仍须落在 schema 中，不得输出 prose invariants。
★机械修订铁律：可选字段不适用就省略或置 null；range 只允许放在 numeric 字段且须为两个不同 JSON 数字，reference 绝不能用 range 表示目标类型或基数；monotonic 只写 up/down；所有 relation/event min_count>=1；跨类型 relation.field 必须真实属于 from/to 中恰好一个端点且 kind=reference；每个 reference 字段必须且只能绑定一种 relation；event effect 只能 SET 已声明且非 relation-owned 的字段，不能“创建实体”或引用另一字段作为值；不要添加 schema 外键。
★候选已经通过 few-shot 观察闭包。候选中承接字面观察的字段及其 kind/unit/monotonic/range 是冻结硬事实：不得删除、改名、翻译或改约束。你只能在保留它们的前提下修订世界骨架。
★person/category/text 等已观测显示字段不是 relation FK。需要结构化同一语义时，保留原字段并另加不同名的 reference 字段；relation.field 只能选 kind=reference 的字段，禁止把已观测字段改型。
只输出 {"review":{"reskin_risk":"low|medium|high","findings":["..."],"decisions":["..."]},"world_blueprint":{...}}。reskin_risk 必须评价【你修订后的版本】；仍为 medium/high 就表示尚未批准。review 是白皮书审议记录，world_blueprint 是修订后的完整可执行契约。""",

    "council.world_review_user": """【场景描述】$desc
【few-shot 文档】$fs
【架构师候选 world_blueprint】$candidate
请完成反方评审，保留审议结论并输出修订后的完整 world_blueprint，严格 JSON。""",

    "council.world_repair": """你是【world_blueprint schema 修理员】，不是世界架构师。候选世界的领域意图已经确定；你只能依据机械错误清单修复 JSON 契约，不能删掉实体/关系/事件来逃避校验，也不能重新发挥另一套世界。
逐项复核：
- 恰一个 primary=true；其余 false。
- 每个 relation 的 from→to 保留领域自然语义；field 必须在两个端点类型中的【恰好一端】逐字找到，编译器会把该端判为 FK owner、值写成另一端。自关系由 source 持有。不要为了字段 owner 颠倒“Boss→掉落装备”“阵营→控制地区”等自然方向。
- relation.field 必须 kind=reference，且每个 reference 字段必须且只能绑定一种 relation（禁止悬空或复用）；event effect 不得再写 relation-owned 字段。静态关系每个 owner 最多承载一个实例；时变关系也必须产生真实引用变化，不能重复同值凑 min_count。
- 显式 v1 的每种 relation/event 都必须 min_count>=1；不允许靠设为 0 逃避实例化。场景描述明确分开的核心对象、关系和事件必须保留，不得偷换端点或合并动作。
- 每个 event role/effect 引用闭合；effect 只能写该 role 类型已经声明的字段。
- range 只放 numeric，且为两个不同 JSON 数字；reference/status/category/text 不写 range。monotonic 只写 up/down。
- 跨类型同名字段约束若不同，改成领域清楚的不同字段名，并同步全部引用。
- 错误若指出“未覆盖/改写 few-shot 字段”，必须把该字段名及明确的 kind/unit/monotonic/range 逐字恢复到合理类型；不得用英文翻译或近义词替代。
- person/category/text 等已观测显示字段必须原样保留；若还要建立关系，新增不同名的 reference 字段承载，禁止改型或把非 reference 字段直接用作 relation.field。
- v1 禁止 prose invariants；候选里若出现 `invariants`，必须删除该键或置为空数组，把能表达的约束改写进 fields/relations/events/causal_rules。
只输出 {"world_blueprint":{...}} 完整 JSON。""",

    "council.world_repair_user": """【机械错误】
$errors
【待修候选】
$candidate
严格逐项修复并输出完整 JSON。""",

    "council.critic": """你是中央办公室【批判员】。审一份白皮书,挑硬伤并直接产出【修订后的完整白皮书 JSON】(不是 diff,照原 schema):
1. 域解析贴合 few-shot 吗?inferred 的有没有不合理(违反怀疑缰绳)?
2. active_lines 是否真贴合本场景天然结构、是否制造了区分度(别什么场景都只配 L1)?gt 都可行吗?
3. shared_world_spec 的实体/关系/周数撑得起激活的产线 + 目标题量吗?关系够 L2 用吗?
4. medium 选得对吗?style_spec 能指导渲染吗?
★【产线 id 铁律】active_lines/line_mapping 里每条的 "line" 必须【逐字照抄】下面这套 canonical id,严禁重命名、自造名、合并或删行(你只能调 weight/why,认为某线不该激活也保留该行并说明):
$taxonomy
★【轴字段铁律】preference_axis 若存在，必须指向 world_blueprint 已声明且归属唯一 entity_type 的真实字段；能力线只读该自然轨迹，严禁另造/改写偏好时间线。
★【字段约束铁律】field_schema 各字段的【名字】及其上的 unit / monotonic / range 约束逐字保留,不得删、不得改、不得改名(它们是下游 gold 正确性的硬依赖;同名字段的 unit/mono/range 若被你改动代码会以草案为准还原,但改名无法自动还原、会丢约束,所以务必别改名)。
★【世界骨架铁律】world_blueprint 是领域架构师已通过机械校验的可执行契约；整块逐字保留，严禁删除、改写、扁平化或按 active_lines 反推重构。代码也会强制以草案版本覆盖。
照原 schema 输出修订版完整白皮书 JSON(保留 preference_axis / state_machines 等 domain_profile 字段,不得丢)。""",

    # ── 议会 user 模板:7 视角共用($desc $fs $ask)+ 批判($desc $fs $draft)──
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
