# Public data boundary

本仓库公开的是历史研究运行记录的**脱敏副本**，不是原始工作目录的逐字镜像。

## 收录范围

- `output/runs/`：45 个 Benchmark 生成 Run，保留所有可读取的 JSON、JSONL 和日志文本。
- `output/eval/`：历史 `eval_*` 目录与对应报告；不包含 embedding、QA cache 和 stale binary cache。
- `docs/blind-reviews/`：现存盲审报告、机读裁决与复核脚本。
- `examples/q099_case.json`：宣传片使用的 Q099 经审计个案。

## 脱敏规则

公开导出器会确定性替换：

- `.env` 中的任何非空配置值；
- API Key、Bearer token、Authorization 和私钥形态；
- 第三方 endpoint 与本机绝对路径；
- 邮箱、手机号；
- 校验位自洽的合成身份证、Luhn 自洽的合成银行卡；
- 登录口令、password、secret 及结构化敏感字段。

同一敏感值会映射到稳定占位符，以尽量保留共指关系。`.env` 本身从不进入公开导出。

每个源文件与公开文件的 SHA-256、尺寸及替换计数记录在 [`output/PUBLIC_EXPORT_MANIFEST.json`](../output/PUBLIC_EXPORT_MANIFEST.json)。源哈希用于追溯，不代表公开副本与内部原件内容相同。

## 研究口径

- 所有场景内容均按合成研究数据处理；脱敏仍用于避免字符串被误认为真人信息或真实凭据。
- 完整 prompts、GT、evidence 和预测一经公开，这批记录即属于 development/debug artifacts，不能用于无泄漏评测。
- `office__20260717-064826` 盲审为 C−，至少 17/181 题存在硬问题；不得称为已发布金标 Benchmark。
- 多个六场景 Run 的闭环目标为 `UNMET`，失败与中断记录特意保留，用于理解流水线演进。
- `office__20260608-160053` 曾被多轮运行覆盖；现有盲审与当前目录并非严格一一对应。

## 未收录

- 密钥和本地 `.env`；
- 二进制向量、模型与缓存；
- 虚拟环境、依赖目录和构建缓存；
- 与 Benchmark Run 无关的内部资料；
- `output/memos_grounding/` 等独立研究语料，它们不是本演示的 Run 记录。
