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

## Phase 1：本地数据体系

- `scripts/xunji_client.py`：读训练（full）、读计划 gzip、限频、鉴权自 `XUNJI_API_KEY`
- `scripts/sync_training.py`：增量按日写 `data/raw/training/YYYY/YYYY-MM-DD.json`，更新 SQLite 与 `data/sync_state.json`
- `scripts/sync_plan.py`：list/get → `data/plans/`
- `scripts/normalize.py`：展平 set 级记录入 SQLite
- 写回仅封装，不做默认 CLI

验收：同步一周 Raw 齐全；SQLite 可查次数/RPE/容量；重复 sync 幂等且限频。

## Phase 2：AI 分析体系

补全 `AGENTS.md` 与 `rules/training-data.md`；周报模板；可选 `export.py`。验收：本地数据分析并写入 `reports/weekly/YYYY-Www.md`。

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
