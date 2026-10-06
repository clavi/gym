# 训练数据领域规则

供 `AGENTS.md` 与 Cursor Skill 共用。完整接口细节见 `.cursor/skills/xunji-all-skills.md`。

## 数据分层

1. **Raw JSON**：API 原样归档，`include_full_data: true`，不修改  
2. **SQLite**：统计与趋势查询  
3. **Markdown**：知识与结论（reports / analysis / notes）

## 鉴权与限频

- Key：`XUNJI_API_KEY`（Bearer 或 `x-api-key`）
- 同一用户同一训练日：普通读 15s、`include_full_data` 读 30s、写回 45s
- 官方计划 list/get：按 Key、操作、计划实例 15s
- 遇 `too frequent`：按 `retry_after_ms` / 提示等待后再试

## 读取训练

- `POST https://trains.xunjiapp.cn/api_trains_for_llm_v2`
- Body 含 `schema_version: train_open_api_v2`、`datestr`、`include_full_data`
- 本地归档始终 `include_full_data: true`（未完成组、RPE、备注、休息、心率等）
- 成功时核心数据在 `res`；不要求 `success === true`
- 按 `datestr` 缓存；同一天不要无故重复请求

## 官方计划

- `POST https://api.xunjiapp.cn/open/plan/query_gzip`（响应可能 gzip）
- `action: list` → `res.plans`；`action: get` + `plan_ref` + 日期范围 → `res.days`
- `platform:N` 与 `universal:N` 是不同计划实例；自定义日期范围最多 92 天

## 写回（须用户确认）

- `POST .../api_upsert_trains_for_llm_v2`
- 动作只用官方中文 `name`；不确定时查 https://github.com/Foveluy/Xunji-movements
- 单次最多 4 条训练且同一天；每条最多 15 动作；每动作最多 20 组
- 更新保留 `localid` / `start` / `end`（除非用户要改时间）
- RPE 合法字符串：`6`…`10` 含半档；清空用 `""`；`difficulty` 只用 `easy|normal|hard`
- 原生有氧日：`cardio: true`，指标在 movement 顶层 `metrics`，不要传 `sets`

## 标准化字段建议（set 级）

`date`, `training`, `movement`, `set`, `weight_kg`, `reps`, `rpe`, `done`, `rest_seconds`  
另兼容超级组 `items[]`、递减组、有氧 `metrics`、心率摘要。
