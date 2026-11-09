---
name: xunji-training
description: >-
  训记训练数据本地同步与分析。在同步训记训练/计划、查询本地 SQLite、
  分析一周训练、生成周报，或用户提到训记时使用。密钥只从 XUNJI_API_KEY 读取。
---

# 训记训练数据 Skill

## 使用前

1. 读取项目 [AGENTS.md](../../../AGENTS.md) 与 [rules/training-data.md](../../../rules/training-data.md)
2. 完整 HTTP 示例与写回细节见 [../xunji-all-skills.md](../xunji-all-skills.md) 中「训记训练数据 Open API Skill」一节
3. 密钥：环境变量 `XUNJI_API_KEY`，禁止写入仓库

## 分析一周

1. 读 [rules/analysis.md](../../../rules/analysis.md)
2. 跑 `python scripts/export.py --week YYYY-Www` 得到事实底稿
3. 对照 `reports/weekly/`、`notes/`、`plans/current.md` 与用户讨论
4. 确认后再把判断写入周报第六、八、九、十节；事实节用 `export.py --write`
5. 仅在用户明确要求同步时运行 `scripts/sync_training.py` / `scripts/sync_plan.py`
6. 写回前展示摘要并等待确认

## 归档约定

- Raw：`data/raw/training/YYYY/YYYY-MM-DD.json`
- 计划：`data/plans/`
- 同步状态：`data/sync_state.json`
- 本地归档请求使用 `include_full_data: true`
