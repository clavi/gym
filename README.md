# gym

训记训练数据本地仓库：同步 → Raw JSON / SQLite → AI Agent 分析 → Markdown 知识沉淀。

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # 填入训记 App 申请的 XUNJI_API_KEY
```

同步训练（默认最近 7 天，最近 3 天强制重拉；full 读间隔约 30s）：

```powershell
python scripts/sync_training.py --days 7
python scripts/sync_training.py --start 2026-10-01 --end 2026-10-06
```

同步官方计划：

```powershell
python scripts/sync_plan.py
python scripts/sync_plan.py --start 2026-10-01 --end 2026-10-31
```

仅从已有 Raw 重建 SQLite：

```powershell
python scripts/normalize.py --date 2026-10-06
python scripts/normalize.py --all
```

## 目录

| 路径 | 用途 |
|------|------|
| `data/raw/` | 训记 API 原始 JSON（本地，不入库） |
| `data/plans/` | 官方训练计划 |
| `data/training.db` | SQLite 分析库（本地，不入库） |
| `data/sync_state.json` | 同步状态（本地，不入库） |
| `reports/` | 周/月/年训练总结 |
| `analysis/` | 动作、肌群、恢复分析 |
| `notes/` | 个人训练知识与决策 |
| `plans/` | 当前与历史训练计划（Markdown） |
| `scripts/` | 同步与标准化脚本 |
| `rules/` | Agent 无关的领域规则 |
| `doc/design/` | 设计与开发计划 |

## 示例查询

```powershell
.\.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/training.db'); print(c.execute('select datestr,count(*) from training_session group by datestr').fetchall())"
```

## 文档

- [本地化分析方案](doc/design/discuss.md)
- [开发计划](doc/design/dev-plan.md)
- [AGENTS.md](AGENTS.md) — 给 AI Agent 的项目约定
