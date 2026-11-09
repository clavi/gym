# 分析工具层

2026-11-07 首版；2026-11-08 真实响应兼容性复核修正。依据 [analysis-workflow.md](analysis-workflow.md) v0.4，指标口径 `analysis-metrics-v2.1`。

## 入口

```powershell
# 默认只读本地，一致性内存快照，不初始化活动库、不写周报
python scripts/export.py --week 2026-W44
python scripts/export.py --week 2026-W44 --db data/training.db --raw-root data/raw

# 先编辑任务卡的日期、问题及事先约定的条件；例卡不代表个人目标已确认
python scripts/analysis_run.py create --task doc/examples/analysis-task.json

# 使用 create 输出的完整目录替换下方 <run>
python scripts/analysis_run.py verify <run>
python scripts/analysis_run.py replay <run>
python scripts/export.py --week 2026-W44 --run <run>
```

`create` 只写 `data/normalized/analysis/<UTC时间_唯一后缀>/`。不联网、不导入 Raw 到活动库、不修改 Raw、旧报告、计划或笔记。需要离线入库时，先单独按访问层文档执行 `reindex`；工具遇到未入库/不一致来源会标记，而非自动修库。

`export --write` 才写正式周报。已有文件仍须 `--force`；现在会保留第六、八、九、十节全文。无法识别章节时拒绝覆盖，不把 `--force` 当作删除讨论的许可。

## 运行包与重算

- SQLite backup API 提取一致快照，包括已经提交的 WAL；只在副本新增 `analysis_result` 派生事实表。正式导出从此表读取，旧 SQL 容量计算不再进入周报。
- 保存 `manifest.json`、`source.sqlite`、`metrics.json`、可选 `daily.csv`、`exclusions.csv`、`facts/`、`inputs/`、`queries/`、事实占位 `review.md`；训练模块另有 `report.md`。
- `inputs/` 保存实际指标定义、结构规则、带依据的语义映射和任务背景，不仅保存版本字符串。`facts/sources.json` 保存实际使用的来源内容/投影、Raw 校验和及定位信息；四周历史展示所需的来源也冻结。
- `queries/` 保存实际工作区 Python 文件和参数。`replay` 使用包内源码、源库及输入重算，核对机器事实、派生事实、CSV 和报告；不读取活动库、当前 Raw 或最新版映射。
- `verify` 检查冻结文件 SHA-256；`manifest` 自身及可追加的复核判断稿不参与递归哈希。不完整运行保留 `incomplete.json`，不能当作已验收产物。修正输入/口径须新建运行，任务卡可填写 `replaces` / `revision_reason`。
- 不向模型自动发送数据库。产物包含健康隐私，处于 Git 忽略目录；示例任务卡不包含个人结论或密钥。
- 请求归档原来没有可靠的 API 拉取时刻时，`capture_time` 保留 `unknown`；文件名时间另记为 `archive_time`，不把它或索引 `updated_at` 冒充 API 采集时点。

## 支持范围与保守降级

| 模块 | 当前行为 |
|---|---|
| 训练计数 | 次数、天数、`normalized_row` 完成记录段分开；不按标题猜训练类型 |
| 结构 | 普通组、简单递减链、简单超级组子目标及整轮；不继承缺失子项完成标志；父子冲突单列；热身传播；嵌套复合结构降级 |
| 负重语义 | 严格有限数解析；明确 kg、lb/lbs 或 `weight_kg`；单位缺失不默认 kg；不按名称判断外部/自重/辅助重量，不乘2 |
| 容量 | 数值、八类状态、候选/可算段、来源覆盖和原因一起输出；有氧没有组行也进入判定；可用 `merge_volume_summaries` 合并充分统计证据，不能只加日级数值 |
| RPE | 独立完成非热身抗阻段，6—10、半档步进；父级共享评分不复制；分母未知则覆盖率 null |
| 动作重量 | 只描述经验证的正外部最大重量，热身/辅助不进入；未确认器械/执行条件时不作进退步/PR 判断 |
| 周比较 | 日历/已过/同步覆盖天数与记录完整性分别列出；没有完整覆盖/记录确认时只描述 |
| 饮食 | 逐营养素有效日均值、同日有符号目标差值及正目标比率；各自日期集合/分母；零目标须确认；达标率另需记录完整和容差依据，按目标变化分期 |
| 身体 | 同类型同单位实测点及两实际日期差值；窗口均值另需预登记频率、最大间隔和可比测量条件，不填补日期 |
| 模板 | 冻结当前内容、tombstone 与游标状态；历史版本/排期/唯一目标匹配解释器尚未实现，执行率明确 blocked/null |
| 联合 | 各域先按日聚合，再以完整日期轴连接；J1/J2/J3 仅提供描述时间线/限制，不自动输出因果解释 |

**特别注意**：当前 API 可能没有明确负重角色。工具不会因为 `unit=kg` 就认定是可计外部重量。没有依据时容量/最大重量降级；记录数量仍可使用。自定义语义映射必须有证据，不能为了得到容量数字随意填映射。

真实响应兼容性复核还确认：普通组也可能带 `time` 和 `timeLabel`，`time` 并不能证明该动作属于计时训练；`exetype` 的未登记枚举也不能直接翻译为负重语义。当前结构规则 `training-semantics-v1.1` 遇到这类歧义时保留原字段并降级，直到显式 `movement_type` 或带依据的映射解除。未知热身标志、截断来源及未登记的有氧摘要也不会被静默筛掉后宣布完整。次数与记录段不会因此一起停止。

可选 `--semantics FILE.json` 格式（仅合成示例，不代表实际动作已经核实）：

```json
{
  "合成测试动作": {
    "movement_type": "resistance",
    "load_role": "external",
    "unit": "kg",
    "laterality": "source_recorded_load_no_multiplier",
    "evidence": "具体来源字段、规则或用户确认及适用日期"
  }
}
```

映射仅在任务窗口内使用；冻结的历史周展示不应用当前任务的映射，除非其日期适用范围同样已确认。

任务卡可选字段：

- `target_validity[日期][calories|protein|carb|fat]`：`state` 为 `valid_zero` / `unset` / `unknown` / `invalid`，并保存 `evidence`。有效正目标默认采用来源值。
- `recording_completeness[training|food][日期]`：`state` 为 `complete` / `incomplete` / `unknown`，已确认状态应给 `evidence`。
- `food_tolerance[营养素]`：`absolute`（kcal 或 g）或 `relative`（无量纲），及 `evidence`。零目标只能使用绝对容差；两者都提供时采用绝对容差。
- `body_window`：`minimum_measurements`（至少2）、`maximum_gap_days`、`comparable_conditions_evidence`。
- `baseline`、`joint_window`、`minimum_samples`、`inclusion_rules`、`exclusion_rules`、`revision`、`replaces`、`revision_reason`：在查看比较结果前登记，未实现的专题比较保持描述/阻断，不推断条件已满足。

当前训练/饮食来源会核对最新有效 Raw 与库内投影；身体只对完整分页链及其记录集合一致时确认相应范围/类型的覆盖，不声称服务端原子快照。模板历史解释仍未实现；复杂嵌套组、自动动作可比性/历史排期及个人恢复判断也没有自动准入。

## 验证

```powershell
python -m unittest discover -s tests -v
```

`tests/test_analysis.py` 覆盖 T1—T10 的当前实现及降级边界；合成测试使用浮点绝对误差上限 `1e-10`，不在看结果后放宽容差。冻结重算在相同运行环境下精确比较结构和数值。真实完整周的业务复核与用户记录完整性确认仍与工具回归测试分开，不因测试通过而自动宣称全部分析准入。

2026-11-08 验证记录：全套 **86 项离线测试通过**。本地已结束周 `2026-W44` 的修订技术运行 `20261108T041231416059Z_3ae02983bece` 已通过 SHA-256、包内重算和 `export --run` 一致性检查，来源覆盖7/7；真实类型/负重语义未全部核实，容量正确降级为 `null / unverified_semantics`，没有发布个人结论。活动库、所用 Raw、原有报告/笔记/计划未改变。旧试跑保留并追加计算问题修订说明，不覆盖冻结事实。
