---
name: xunji-training
description: >-
  训记训练数据本地同步与分析规则。在同步训记训练/计划、查询本地 Raw 或 SQLite、
  生成训练报告，或用户提到训记 Open API 时使用。密钥只从 XUNJI_API_KEY 读取。
disable-model-invocation: true
---

# 训记训练数据 Skill

## 使用前

1. 读取项目 [AGENTS.md](../../../AGENTS.md) 与 [rules/training-data.md](../../../rules/training-data.md)
2. 完整 HTTP 示例与写回细节见 [../xunji-all-skills.md](../xunji-all-skills.md) 中「训记训练数据 Open API Skill」一节
3. 密钥：环境变量 `XUNJI_API_KEY`，禁止写入仓库

## 本地优先

- 分析时先读 `data/raw/`、`data/training.db`、`reports/`、`notes/`
- 仅在用户明确要求同步时运行 `scripts/sync_training.py` / `scripts/sync_plan.py`（Phase 1 实现后）
- 写回前展示摘要并等待确认

## 归档约定

- Raw：`data/raw/training/YYYY/YYYY-MM-DD.json`
- 计划：`data/plans/`
- 同步状态：`data/sync_state.json`
- 本地归档请求使用 `include_full_data: true`
