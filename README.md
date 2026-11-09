# gym

训记训练数据本地仓库：同步 → Raw JSON / SQLite → AI Agent 分析 → Markdown 知识沉淀。

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # 填入训记 App 申请的 XUNJI_API_KEY
```

同步训练（默认续接上次同步；首次默认最近 7 天，最近 3 天追加刷新快照；full 读间隔约 30s）：

```powershell
python scripts/sync_training.py --days 7
python scripts/sync_training.py --start 2026-10-01 --end 2026-10-06
```

同步官方计划：

```powershell
python scripts/sync_plan.py
python scripts/sync_plan.py --start 2026-10-01 --end 2026-10-31
```

导出某一 ISO 周的事实周报（判断段落留空，待讨论）：

```powershell
python scripts/export.py --week 2026-W40
python scripts/export.py --week 2026-W40 --write
```

仅从已有 Raw 重建 SQLite：

```powershell
python scripts/normalize.py --date 2026-10-06
python scripts/normalize.py --all
```

## 统一数据访问

训练、饮食、身体指标和 Agent 个人模板统一通过本地优先访问层读取；默认不联网，显式 `--sync` 才允许请求接口。

```powershell
python scripts/data_access.py food --start 2026-11-04 --end 2026-11-04 --json
python scripts/data_access.py body --start 2026-10-01 --end 2026-11-06 --types weight bodyfat --sync
python scripts/data_access.py templates --sync
python scripts/data_access.py reindex  # 纯离线归档入库/恢复游标
python -m unittest discover -s tests -v
```

新读取均保留不可覆盖的 Raw 快照；饮食含每日 `limits`，身体自动分页，个人模板按持久游标增量同步（仅 Agent 专属文件夹）。各域使用独立 Key，详见 [数据访问层文档](doc/data-access.md)。

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
- [联合分析流程（v0.4）](doc/analysis-workflow.md)
- [分析工具层：冻结底稿、指标与离线重算](doc/analysis-tools.md)
- [联合分析流程 v0.2 复核记录](doc/analysis-workflow-review-v0.2.md)
- [AGENTS.md](AGENTS.md) — 给 AI Agent 的项目约定
