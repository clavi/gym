# 训记训练数据本地化分析 — 开发计划（面向 AI）

依据：[discuss.md](discuss.md)、[`.cursor/skills/xunji-all-skills.md`](../../.cursor/skills/xunji-all-skills.md)。

## 范围

- 仓库 `gym` 即本地训练数据项目
- 第一期：训练记录 + 官方计划
- 饮食 / 身体 / Agent 模版、MCP、公网后置
- API Key 只在 `.env`；文档与 Skill 不得含真实 Key

## 架构

```text
训记 Open API → scripts sync → Raw JSON + plans + SQLite
                                      ↓
                                 AI Agent
                                      ↓
                          reports / analysis / notes
```

## Phase 0：项目骨架（已完成）

目录、`.gitignore`、`.env.example`、`requirements.txt`、`AGENTS.md` 占位、`rules/`、报告模板、Skill 去密钥。已推 `main`；后续在 `develop` 开发。

## Phase 1：本地数据体系（已完成）

- `scripts/xunji_client.py`：读训练（full）、读计划 gzip、限频、鉴权自 `XUNJI_API_KEY`；`upsert_trains` 仅封装
- `scripts/sync_training.py`：增量按日写 `data/raw/training/YYYY/YYYY-MM-DD.json`，更新 SQLite 与 `data/sync_state.json`
- `scripts/sync_plan.py`：list/get → `data/plans/`
- `scripts/normalize.py` / `scripts/db.py`：展平 set 级记录入 SQLite

验收：`python scripts/sync_training.py --days 7`；SQLite 可查次数/RPE/容量；重复 sync 幂等且限频。

## Phase 2：AI 分析体系（已完成）

- `rules/analysis.md`：事实与判断分开；ISO 周报路径；讨论后再写建议
- `scripts/export.py`：从 SQLite 生成周报第一至五节与第七节
- `AGENTS.md`、训练 Skill 指向同一套规则

验收：`python scripts/export.py --week YYYY-Www --write` → `reports/weekly/YYYY-Www.md`。

## Phase 3：知识沉淀（使用节奏）

每周/每月更新 reports、analysis、notes；工程侧保持路径与规则稳定。

## Phase 4–5：后置

Local MCP → 公网 MCP；第一期不做 Web UI / 服务端 / 饮食身体同步 / 自动写回。

## 实施顺序

1. Phase 0 骨架  
2. client + sync_training（Raw）  
3. normalize + SQLite  
4. sync_plan  
5. AGENTS/rules/模板  
6. 闭环：同步 → 分析 → 周报  
7. 可选 export；更后写回 / MCP  

## 约束

- 限频：读 15s / full 30s / 写 45s  
- 本地归档 `include_full_data: true`  
- Raw 只读不改  
- Raw/DB 不提交远程  

## 数据访问层补充（2026-11）

按新的需求扩展第一期只读访问能力：训练、饮食、身体、Agent 个人模板。

- 本地优先统一入口 `scripts/access.py`，命令入口 `scripts/data_access.py`。
- 各域独立鉴权、响应校验、限频、重试与安全错误分类。
- Raw 只追加快照；SQLite schema v2 增加饮食/身体/模板/查询索引，不修改旧 Raw。
- 身体分页、模板游标与 tombstone、事务化归一化、离线重放恢复。
- 保留官方计划读取；本次不扩展远程写入流程，不开发 MCP 或 Web。
- 已按用户要求完成身体查询与 Agent 空文件夹首屏的真实 API 联调；非空模板与多页/删除事件仍以离线契约测试验证。

使用与边界见 [数据访问层文档](../data-access.md)。
