# 分析写作规则

供 `AGENTS.md` 与 Cursor Skill 共用。数字只来自本地库，判断只来自与用户的讨论。

## 分析前先读

1. `data/training.db`（优先跑 `scripts/export.py`，不要通读全部 Raw）
2. 目标周及前几周的 `reports/weekly/`
3. `notes/observations.md`、`notes/decisions.md`、`plans/current.md`
4. 若在比较计划与执行，再读 `data/plans/`

默认不调用训记 API。用户明确要求同步时才跑 `scripts/sync_training.py`。

## 周的定义

- 周报文件名使用 ISO 周：`reports/weekly/YYYY-Www.md`（例：`2026-W40.md`）
- 「最近一周」= 包含今天的 ISO 周；「上一周」= 上一个 ISO 周
- 周一至周日。空的 `trains: []` Raw 表示已确认休息，不是缺数据

## 事实与判断

| 类型 | 位置 | 谁来写 |
|------|------|--------|
| 次数、时长、容量、动作重量 | 周报第一至五节、第七节 | `scripts/export.py` |
| 恢复、发现、下周建议、待观察 | 第六、八、九、十节 | 与用户讨论后再写入 |
| 长期动作/肌群判断 | `analysis/` | 讨论后更新，可跨多周 |
| 个人规律与已拍板决策 | `notes/` | 讨论后追加 |

脚本不得编造 RPE、疲劳或训练建议。库中 RPE 为空就写「无记录」。

## 生成事实底稿

```powershell
python scripts/export.py --week 2026-W40
python scripts/export.py --week 2026-W40 --write
python scripts/export.py --week 2026-W40 --write --force
```

`--write` 写入 `reports/weekly/`。文件已存在时必须 `--force`，避免盖掉讨论后改过的段落。

肌群归类是关键词启发式，定义在 `scripts/export.py` 的 `MUSCLE_RULES`。对不上的动作标「未分类」，不要臆造肌群。训练安排表的「主要肌群」优先用训记当天标题（如胸/背/腿/肩）。

## 讨论后再定稿

1. 把事实底稿给用户看
2. 只把用户确认过的判断写入第六、八、九、十节，以及 `analysis/`、`notes/`
3. 下一周分析时带上本周周报，形成连续上下文

## 容量定义

完成组、非热身、`weight_kg * reps`。重量为 0 或自重未填重量的组计入组数，不计入容量。
