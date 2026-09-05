# Recording guide

## 推荐设置

- 画幅：16:9，优先 3840×2160，最低 2560×1440。
- 帧率：60 fps。
- 浏览器缩放：100%，隐藏书签栏、通知和个人账号信息。
- 每条录屏前后各保留约 1 秒无操作余量，不录系统声音。
- 文件名按镜头编号，例如 `S06_input.mov`、`S07_upload.mov`。

## 启动

```bash
./scripts/setup-demo.sh
./scripts/live-demo.sh
```

无 `.env` 时，历史 Replay 与电影回放可用；真实 Live 会明确显示尚未配置，不会伪装运行。

## 台本与页面对应

| 镜头 | 页面或素材 | 录制动作 | 性质 |
|---|---|---|---|
| 06 | 本地 `LIVE STUDIO` | 输入台本场景描述 | UI 操作 |
| 07 | 本地 `LIVE STUDIO` | 拖入 `examples/office_sample.txt` | UI 操作 |
| 08 | “重放历史” | 选择 `office__20260717-064826` 并播放 | 真实历史 Replay |
| 09 | 电影回放 Scene 1–2 | 议会白皮书、世界构建 | 基于历史 Run 的脱敏重建 |
| 10 | 电影回放 Scene 3–4 | 问题铸造、证据闸门 | 基于历史 Run 的脱敏重建 |
| 11–20 | `examples/q099_case.json` | 按台本制作 Q099、MISS、证据链和验证清单 | 审计个案 + 工作流示意 |
| 21–26 | 台本六场景 brief | 另行制作游戏、Agent、客服、陪伴、助理和知识库素材 | Memory requirement illustration |
| 27–29 | `docs/STORYBOARD.md` | 六宫格、验证回路与品牌片尾 | Workflow illustration |

现有 Memory Arena 页面使用 Q104，不要把它直接剪成台本中的 Q099。Q099 应使用 [`examples/q099_case.json`](../examples/q099_case.json) 的题面、基础答案、gold 与两段证据。

## 必须保留的标识

- Q099 个案：`HISTORICAL INTERNAL RUN · AUDITED CASE RECONSTRUCTION`
- 下一版验证清单：`VERSION REVIEW INPUT`
- 交给 Memory 团队：`WORKFLOW ILLUSTRATION`
- 六场景：`MEMORY REQUIREMENT · ILLUSTRATION`

不要把“验证清单”表现为当前系统已经自动修改 Memory，也不要把历史总分表现为已发布排行榜。
