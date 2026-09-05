# Memory Forge Showcase

Memory Forge 的公开交互演示、真实 Live 流水线和历史研究运行档案。

- 在线五幕回放：<https://netreee.github.io/memory-forge-showcase/>
- 完整台本：[docs/STORYBOARD.md](docs/STORYBOARD.md)
- 录制说明：[docs/RECORDING_GUIDE.md](docs/RECORDING_GUIDE.md)
- 45 个历史 Run：[output/RUN_INDEX.md](output/RUN_INDEX.md)
- 公开数据边界：[docs/PUBLIC_DATA.md](docs/PUBLIC_DATA.md)

## 三种演示模式

- `LOCAL LIVE`：输入场景并上传 UTF-8 TXT，真实执行 Benchmark 工厂，严格运行到 `06_grounded_questions.json`。
- `REPLAY`：选择仓库内已有 `run_id`，按真实阶段和调用记录压缩成约 60 秒回放；不启动模型。
- `RECORDED`：无需后端的五幕电影化回放，用于 GitHub Pages、会议现场和宣传片兜底。

GitHub Pages 默认进入 `RECORDED`。本地一键启动后可使用 `REPLAY`；配置模型环境后才会启用 `LOCAL LIVE`。

## 本地启动

需要 Node.js >= 22.13 和 Python >= 3.10。

```bash
git clone https://github.com/Netreee/memory-forge-showcase.git
cd memory-forge-showcase
./scripts/setup-demo.sh
./scripts/live-demo.sh
```

浏览器打开 <http://127.0.0.1:3000>。不创建 `.env` 也可以浏览全部可回放历史记录。

启用真实 Live：

```bash
cp .env.example .env
# 只在本机填写 OPENAI_API_KEY、OPENAI_BASE_URL 和 MODEL
./scripts/live-demo.sh
```

`.env`、新产生的 `live__*` Run、虚拟环境和依赖目录都不会进入 Git。

## 历史记录

仓库保留了 45 个历史 Run 的公开脱敏副本，包括完整、失败、中断、旧版和定向实验记录；同时保留已有评测结果与盲审材料。七个宣传场景的推荐入口是：

- `office__20260717-064826`
- `game__20260625-112210`
- `agent__20260624-214306`
- `cs__20260625-134047`
- `companion__20260624-234524`
- `assistant__20260625-143946`
- `kb__20260625-032549`

请注意：

- `done` 只表示流程执行结束，不表示质量验收通过。
- Office 0717 的独立盲审结论是 `C− / release blocked`；其他历史 Run 也可能存在 `UNMET`、失败或尚未盲审。
- 题目、GT、证据定位和系统预测作为 public development artifacts 留档，因此这些材料不能再充当 unseen benchmark。
- 所有公开记录均经过自动与人工复核脱敏；详情和逐文件哈希见 [output/PUBLIC_EXPORT_MANIFEST.json](output/PUBLIC_EXPORT_MANIFEST.json)。

## 回放操作

- `Space`：播放或暂停电影回放
- `←` / `→`：上一幕或下一幕
- `R`：重置
- `F`：进入或退出全屏
- `?scene=1` 到 `?scene=5`：直接打开指定电影化场景
- 本地首页切换到“重放历史”：浏览有 manifest 的历史 Run

## 仓库结构

```text
app/ components/ lib/        前端与电影化回放
pipeline/                    可真实执行到 06 的 Benchmark 工厂
tools/                       本地 API、worker 与公开导出工具
scripts/                     安装和一键启动脚本
examples/                    固定录制输入与 Q099 审计个案
output/runs/                 45 个公开脱敏历史 Run
output/eval/                 历史评测记录
docs/blind-reviews/          历史盲审材料
docs/STORYBOARD.md           宣传片台本
```

## 开发

```bash
npm run build
npm run build:github
npm run lint
```

若在包含原始研究数据的主项目旁重新生成公开副本：

```bash
python tools/export_public_data.py --source-root ../memory_bench_factory
```

导出器永远不会复制 `.env`、二进制 embedding/QA cache、虚拟环境或 `node_modules`。
