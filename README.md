# gym

训记训练数据本地仓库：同步 → Raw JSON / SQLite → AI Agent 分析 → Markdown 知识沉淀。

## 快速开始

1. 复制 `.env.example` 为 `.env`，填入训记 App 申请的 API Key  
2. `python -m venv .venv` 并安装依赖：`pip install -r requirements.txt`  
3. Phase 1 完成后：`python scripts/sync_training.py --days 7`

## 目录

| 路径 | 用途 |
|------|------|
| `data/raw/` | 训记 API 原始 JSON（本地，不入库） |
| `data/plans/` | 官方训练计划 |
| `data/training.db` | SQLite 分析库（本地，不入库） |
| `reports/` | 周/月/年训练总结 |
| `analysis/` | 动作、肌群、恢复分析 |
| `notes/` | 个人训练知识与决策 |
| `plans/` | 当前与历史训练计划（Markdown） |
| `scripts/` | 同步与标准化脚本 |
| `rules/` | Agent 无关的领域规则 |
| `doc/design/` | 设计与开发计划 |

## 文档

- [本地化分析方案](doc/design/discuss.md)
- [开发计划](doc/design/dev-plan.md)
- [AGENTS.md](AGENTS.md) — 给 AI Agent 的项目约定
