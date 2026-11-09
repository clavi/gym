# AGENTS.md

本文件供任意 AI Agent（IDEA AI / Cursor / Codex / Claude Code 等）使用本项目时遵循。

详细领域规则见 [rules/training-data.md](rules/training-data.md)。开发阶段见 [doc/design/dev-plan.md](doc/design/dev-plan.md)。

## 项目目标

训记训练数据 → 本地持久化 → AI 分析 → Markdown 知识沉淀。  
**本地数据是核心；AI Agent 可替换。**

## 目录约定

| 路径 | 规则 |
|------|------|
| `data/raw/` | 原始 API 响应，**只读，禁止修改** |
| `data/normalized/` | 标准化中间产物（可选） |
| `data/training.db` | SQLite，复杂统计优先查库 |
| `data/plans/` | 官方计划本地副本 |
| `reports/` | 事实向周/月/年报 |
| `analysis/` | 动作/肌群/恢复判断，可持续更新 |
| `notes/` | 长期个人训练知识与决策 |
| `plans/` | 当前计划与历史（Markdown） |
| `scripts/` | 同步与标准化；优先跑脚本而非手改数据 |

## 何时调用训记 API

- 默认：**不要**每次分析都实时请求训记；先读本地数据。
- 仅在用户明确要求同步/拉取/写回时，通过 `scripts/` 或后续 MCP 调用。
- API Key 只来自环境变量 / `.env`，永不写入报告或提交 Git。

## 分析与写作

细则见 [rules/analysis.md](rules/analysis.md)。

1. 先跑 `python scripts/export.py --week YYYY-Www` 取得事实底稿，再读近期周报、`notes/`、`plans/current.md`
2. 「最近一周」= 含今天的 ISO 周，文件为 `reports/weekly/YYYY-Www.md`
3. 第一至五节、第七节只写库里的数字；第六、八、九、十节以及 `analysis/`、`notes/` 必须经用户确认后再写
4. 已有周报时，`--write` 必须加 `--force`，避免盖掉讨论内容
5. 写回训记前必须展示变更摘要并等待用户确认（第一期默认不做写回）

## 禁止

- 修改 `data/raw/` 内容
- 把密钥写入仓库文件
- 未经确认的训记写回
- 编造未在官方动作表中的中文动作名用于写回
