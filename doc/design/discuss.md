# 训记训练数据接入 AI Agent 的本地化分析方案

## 1. 建设目标

目标不是单独把「训记」接入 ChatGPT，而是建立一套：

> **训记训练数据 → 本地持久化 → AI Agent 分析 → Markdown 知识沉淀**

的个人训练数据分析体系。

AI Agent 可以是：

- IDEA 中的 AI 聊天 / Coding Agent
- Codex
- Claude Code
- ChatGPT
- 其他能够读取本地项目文件或调用 MCP Tool 的 AI Agent

核心原则是：

> **训练数据属于本地长期数据资产，AI Agent 只是分析这些数据的工具。**

因此不将某一个 AI 产品作为系统核心。

---

# 2. 目标使用方式

理想工作方式如下：

```text
训记 App
   │
   │ Open API
   ▼
本地同步程序
   │
   ├── 原始训练数据
   ├── 标准化训练数据
   └── 官方训练计划
   │
   ▼
本地训练数据仓库
   │
   ▼
IDEA / Codex / 其他 AI Agent
   │
   ├── 阅读历史训练
   ├── 分析最近一周
   ├── 分析训练趋势
   ├── 与用户讨论
   ├── 提供训练建议
   └── 形成总结
   │
   ▼
Markdown 文件
   │
   ├── 周训练总结
   ├── 月度训练总结
   ├── 动作分析
   ├── 训练计划调整
   └── 长期训练观察
```

例如在 IDEA 中直接询问：

```text
分析最近一周训练情况，
重点关注训练量、RPE、动作进步和恢复情况。
结合之前几周的数据给出判断。
```

AI Agent 可以直接读取项目中的本地训练数据。

讨论完成后：

```text
把今天的结论整理到
reports/weekly/2026-W41.md
```

最终形成长期积累的个人训练知识库。

---

# 3. 总体设计原则

整个系统建议遵循四个原则。

## 3.1 数据本地优先

训记 API 主要承担：

> 数据来源

而不是每次分析时都实时访问。

首先把数据同步到本地：

```text
训记
 ↓
同步
 ↓
本地数据
 ↓
AI 分析
```

而不是：

```text
AI
 ↓
每次分析都实时请求训记
```

这样可以：

- 避免 API 限频问题
- 减少重复访问
- 保存长期历史数据
- 支持离线分析
- 不依赖某一个 AI 平台
- 更容易进行跨周、跨月趋势分析

---

# 4. 推荐总体架构

建议采用：

```text
                  训记 App
                     │
                     │ Open API
                     ▼
             ┌─────────────────┐
             │ Xunji Sync      │
             │ 本地同步程序     │
             └────────┬────────┘
                      │
                      ▼
        ┌────────────────────────────┐
        │      本地训练数据仓库       │
        │                            │
        │ raw/                       │
        │ normalized/                │
        │ database/                  │
        │ plans/                     │
        └────────────┬───────────────┘
                     │
                     ▼
        ┌────────────────────────────┐
        │         AI Agent           │
        │                            │
        │ IDEA AI                    │
        │ Codex                      │
        │ Claude Code                │
        │ ChatGPT                    │
        │ 其他 Agent                 │
        └────────────┬───────────────┘
                     │
                     ▼
        ┌────────────────────────────┐
        │ Markdown 知识与分析结果    │
        │                            │
        │ weekly/                    │
        │ monthly/                   │
        │ movements/                 │
        │ plans/                     │
        │ observations/              │
        └────────────────────────────┘
```

这里：

> **本地数据仓库才是整个系统的核心。**

AI Agent 可以替换，而数据和分析成果不会丢失。

---

# 5. 与原 MCP 方案的主要变化

原方案核心是：

```text
ChatGPT
 ↓
MCP
 ↓
训记 API
```

调整以后建议变为：

```text
训记 API
 ↓
本地同步
 ↓
本地训练数据
 ↓
AI Agent
```

MCP 不再是必须组件。

MCP 更适合承担：

```text
实时查询
写回训练
跨 Agent 标准化访问
```

但对于：

```text
历史分析
趋势分析
周报
月报
训练讨论
知识积累
```

本地文件更加简单可靠。

---

# 6. 推荐本地项目结构

建议直接建立一个 Git 项目，例如：

```text
training/
│
├── README.md
│
├── AGENTS.md
│
├── .env
│
│
├── scripts/
│   ├── sync_training.py
│   ├── sync_plan.py
│   ├── normalize.py
│   └── export.py
│
├── data/
│   │
│   ├── raw/
│   │   └── training/
│   │       ├── 2026/
│   │       │   ├── 2026-10-01.json
│   │       │   ├── 2026-10-02.json
│   │       │   └── ...
│   │
│   ├── plans/
│   │   └── ...
│   │
│   ├── normalized/
│   │   └── ...
│   │
│   └── training.db
│
├── reports/
│   │
│   ├── weekly/
│   │   ├── 2026-W40.md
│   │   └── 2026-W41.md
│   │
│   ├── monthly/
│   │   └── 2026-10.md
│   │
│   └── yearly/
│
├── analysis/
│   │
│   ├── movements/
│   │   ├── 哑铃卧推.md
│   │   ├── 高脚杯深蹲.md
│   │   └── ...
│   │
│   ├── muscle-groups/
│   │   ├── 胸.md
│   │   ├── 背.md
│   │   └── ...
│   │
│   └── recovery/
│
├── plans/
│   ├── current.md
│   └── history/
│
└── notes/
    ├── training-principles.md
    ├── observations.md
    └── decisions.md
```

---

# 7. 数据建议保留两层

不建议只保存一种格式。

建议同时保存：

> **原始数据 + 标准化数据**

---

## 7.1 Raw 原始数据

每次从训记 API 获取的数据原样保存。

例如：

```text
data/raw/training/2026/2026-10-06.json
```

优点是：

- 保留全部原始信息
- 将来字段变化可以重新处理
- AI 可以核查原始数据
- 避免转换程序造成信息永久丢失

原则：

> Raw 数据尽量不要修改。

---

# 8. 标准化数据

原始 API 数据结构主要面向训记本身。

长期分析时，可以转换成更加稳定的数据模型。

例如每一组训练可以标准化成：

```json
{
  "date": "2026-10-06",
  "training": "上肢训练",
  "movement": "哑铃卧推",
  "set": 3,
  "weight_kg": 22.5,
  "reps": 10,
  "rpe": 8.5,
  "done": true,
  "rest_seconds": 100
}
```

这样以后计算：

```text
动作训练量
周训练量
平均 RPE
重量趋势
次数趋势
训练容量
```

都会容易很多。

---

# 9. 是否需要数据库

建议使用：

> **Raw JSON + SQLite**

而不是二选一。

Raw JSON 负责：

```text
完整留档
```

SQLite 负责：

```text
统计查询
趋势分析
```

例如：

```text
data/training.db
```

可以包含：

```text
training_session
movement
training_set
cardio_record
heart_rate_summary
training_plan
```

对于 AI Agent：

简单问题可以直接阅读 JSON。

复杂问题可以让 Agent：

```text
查询 SQLite
+
读取 Markdown 历史分析
```

---

# 10. 为什么推荐 SQLite

随着训练历史增加，很快会出现类似问题：

```text
过去半年卧推重量增长多少？
```

或者：

```text
过去12周胸部平均每周多少有效组？
```

再例如：

```text
找出所有 RPE >= 9 且下一次训练成绩下降的情况。
```

如果全部靠 AI 遍历 JSON：

```text
效率低
上下文大
容易遗漏
```

SQLite 则非常适合这种个人结构化数据。

因此建议：

```text
JSON
=
数据档案

SQLite
=
分析数据库

Markdown
=
知识和结论
```

三者职责不同。

---

# 11. API Key 管理

不建议继续把真实 API Key 写到 Skill 或 Markdown 中。

建议：

```text
.env
```

例如：

```text
XUNJI_API_KEY=xxxxxxxx
```

并加入：

```text
.gitignore
```

例如：

```text
.env
data/raw/
data/training.db
```

是否把训练数据提交 Git，可以根据个人隐私需求决定。

如果只在自己的电脑使用：

> 推荐训练原始数据不提交远程 Git。

Markdown 分析结果则可以根据需要决定是否纳入 Git。

---

# 12. 本地同步程序

建议建立：

```text
scripts/sync_training.py
```

负责：

```text
读取训记 API
↓
检查本地缓存
↓
获取缺少日期
↓
保存 Raw JSON
↓
更新 SQLite
```

例如：

```bash
python scripts/sync_training.py --days 7
```

或者：

```bash
python scripts/sync_training.py \
  --start 2026-10-01 \
  --end 2026-10-06
```

---

# 13. 增量同步

同步程序不应该每次重新读取全部历史。

推荐维护：

```text
last_sync
```

例如：

```json
{
  "last_sync": "2026-10-06T10:30:00",
  "last_training_date": "2026-10-06"
}
```

平时只同步：

```text
最近几天
+
可能被修改的训练
```

例如默认：

```text
今天
昨天
前天
```

历史数据通常无需重复请求。

---

# 14. 完整数据策略

建议本地保存时直接采用：

```text
include_full_data: true
```

原因是本地数据仓库的目标不是临时查看，而是长期分析。

完整数据可能包括：

- 未完成组
- RPE
- difficulty
- 训练备注
- 左右侧重量
- 实际训练秒数
- 预设休息时间
- 实际休息时间
- 心率数据摘要
- 有氧指标
- 超级组
- 递减组

如果本地只保存轻量数据，将来做深入分析时还要重新请求历史记录。

因此：

> **本地归档建议优先完整数据。**

---

# 15. 心率数据

完整同步时保存：

```text
trains[].heartRate
```

以及动作级：

```text
metrics.avgHeartRate
metrics.maxHeartRate
metrics.minHeartRate
sets[].heartRate
```

不必追求原始秒级心率。

训记 API 已提供压缩趋势：

```text
values
step
avg
max
min
duration
```

对于个人训练分析已经足够。

---

# 16. 官方训练计划也应本地保存

不仅同步实际训练，也建议同步：

```text
PlatformPlan
UniversalPlan
```

保存到：

```text
data/plans/
```

这样 AI Agent 可以分析：

```text
计划
VS
实际执行
```

例如：

```text
本周计划：

周一 A
周二 B
周四 A
周五 B

实际：

周一 A
周二 B
周五 A

完成率：75%
```

进一步分析：

```text
是否经常跳过腿部训练
是否连续训练过多
计划训练量与实际训练量差异
```

---

# 17. AI Agent 的角色

AI Agent 不承担训练数据存储职责。

AI 主要负责：

```text
理解
+
分析
+
讨论
+
推理
+
形成建议
+
形成 Markdown
```

例如：

```text
用户
↓
最近肩膀恢复比较慢，是不是训练安排有问题？
↓
AI Agent
↓
读取最近4周训练
↓
读取肩部动作记录
↓
读取之前训练讨论
↓
分析训练频率、RPE和训练量
↓
与用户讨论
↓
形成结论
```

---

# 18. IDEA 中的推荐工作模式

整个：

```text
training/
```

目录直接作为 IDEA 项目打开。

AI Agent 就工作在这个项目中。

例如询问：

```text
读取 data 中最近7天训练数据，
同时参考最近4周 weekly report。

分析：

1. 本周训练量
2. 各肌群训练频率
3. RPE
4. 训练容量变化
5. 疲劳和恢复情况
6. 与前几周相比是否进步

最后给出下周建议。
```

AI 完成分析以后继续说：

```text
我们讨论一下你的结论。
```

经过几轮讨论后：

```text
把最终结论写入：

reports/weekly/2026-W41.md
```

这比每次聊天结束以后信息消失更适合长期训练管理。

---

# 19. Markdown 不只是报告

Markdown 建议分成三类。

## 第一类：事实总结

例如：

```text
reports/weekly/
```

主要记录：

```text
本周训练次数
训练量
训练动作
RPE
训练时长
趋势
```

尽量客观。

---

## 第二类：分析判断

例如：

```text
analysis/
```

记录：

```text
卧推长期趋势
肩部恢复问题
腿部训练量是否不足
训练计划合理性
```

这部分可以持续更新。

---

## 第三类：个人训练知识

例如：

```text
notes/
```

记录长期形成的结论：

```text
我适合的卧推训练量
我连续训练几天容易疲劳
午间训练强度边界
动作替代方案
恢复规律
```

这实际上逐渐形成：

> **个人训练模型**

---

# 20. 周报 Markdown 建议结构

例如：

```markdown
# 2026-W41 训练总结

## 一、本周概况

训练次数：

力量训练：

有氧：

总训练时长：

---

## 二、训练安排

| 日期 | 训练 | 主要肌群 |
|---|---|---|

---

## 三、各肌群训练量

### 胸

### 背

### 腿

### 肩

### 手臂

---

## 四、主要动作变化

### 哑铃卧推

### 深蹲

### 划船

---

## 五、训练强度

平均 RPE：

高 RPE 组：

---

## 六、恢复情况

---

## 七、与前4周比较

---

## 八、主要发现

---

## 九、下周建议

---

## 十、需要继续观察的问题
```

---

# 21. 形成连续分析，而不是孤立周报

下一周分析时，AI 不只是读取训练数据。

应该同时读取：

```text
本周训练数据
+
过去几周 weekly report
+
长期 observations
+
current plan
```

于是分析形成连续上下文。

例如：

```text
第1周：
感觉恢复不足

第2周：
减少胸部训练2组

第3周：
RPE下降，成绩不下降

结论：
之前训练量可能略高
```

这比单次聊天非常有价值。

---

# 22. 建议增加 AGENTS.md

项目根目录建议提供：

```text
AGENTS.md
```

用于告诉各种 AI Agent：

> 如何使用这个训练项目。

例如定义：

```text
数据目录在哪里
Raw 数据不可修改
SQLite 数据如何查询
如何生成周报
如何更新分析
什么时候可以调用训记 API
哪些修改必须用户确认
```

这样：

```text
IDEA AI
Codex
Claude Code
其他 Agent
```

都可以使用相同规则。

这比把规则绑定到某个平台的 Skill 更通用。

---

# 23. Skill 与 AGENTS.md 的关系

原来的训练 Skill 仍然有价值。

可以抽象成：

```text
核心训练规则
```

然后分别提供：

```text
AGENTS.md
SKILL.md
```

二者可以共享主要规范。

例如：

```text
rules/
└── training-data.md
```

然后：

```text
AGENTS.md
引用这些规则

SKILL.md
也基于这些规则
```

最终形成：

> **Agent 无关的领域规则。**

---

# 24. MCP 是否还需要

仍然建议保留 MCP 的可能性，但放到第二阶段。

对于 IDEA 本地分析：

> **MCP 不是必需的。**

因为：

```text
IDEA AI
 ↓
读取本地文件
```

已经足够。

但 MCP 在三个场景比较有价值。

---

# 25. 场景一：实时获取最新数据

例如用户说：

```text
把今天刚练完的数据同步下来。
```

AI 可以调用：

```text
sync_training
```

MCP Tool。

这样 AI 不需要自己运行脚本。

---

# 26. 场景二：写回训记

例如：

```text
把今天卧推最后一组 RPE 改成 8.5。
```

流程：

```text
AI
↓
读取本地训练
↓
生成修改
↓
展示差异
↓
用户确认
↓
MCP
↓
训记 API
```

这种操作使用 MCP 很合适。

---

# 27. 场景三：其他 AI Agent

如果以后某个 AI Agent：

```text
无法访问 IDEA 项目
```

但支持 MCP：

```text
AI Agent
 ↓
MCP
 ↓
本地/云端训练服务
```

仍然可以访问同一套训练能力。

所以：

> MCP 应作为统一能力接口，而不是整个系统的数据中心。

---

# 28. 是否需要公网 MCP

按照新的本地优先方案：

> **第一阶段完全不需要公网 MCP Server。**

可以直接：

```text
IDEA
 ↓
本地文件
```

甚至训记数据同步也只是：

```text
Python Script
 ↓
公网训记 API
```

没有任何服务端。

如果以后使用本地 Agent + 本地 MCP：

```text
AI Agent
 ↓
localhost MCP
```

同样无需公网。

只有希望：

```text
云端 AI Agent
```

主动访问自己的 MCP 时，才需要提供：

```text
公网 HTTPS MCP
```

因此公网部署可以明显后置。

---

# 29. 推荐技术路线

第一阶段建议只实现：

```text
Python
+
httpx
+
SQLite
+
JSON
+
Markdown
```

暂时：

```text
不部署服务器
不做公网 MCP
不做复杂 Web UI
```

整个系统完全运行在个人电脑。

---

# 30. 第一阶段系统

架构非常简单：

```text
              训记 API
                 │
                 ▼
        Python Sync Script
                 │
          ┌──────┴───────┐
          ▼              ▼
       Raw JSON        SQLite
          │              │
          └──────┬───────┘
                 ▼
             AI Agent
                 │
                 ▼
             Markdown
```

这已经可以解决大部分需求。

---

# 31. 第二阶段

当第一阶段稳定以后，再增加：

```text
Local MCP Server
```

例如：

```text
localhost:8000/mcp
```

提供：

```text
sync_training
get_training
get_training_range
get_training_plan
query_training_stats
upsert_training
```

这样不同 AI Agent 可以统一访问训练数据。

---

# 32. 第三阶段

如果以后希望：

```text
ChatGPT 网页端
手机端 AI
远程 Agent
```

直接访问数据，再考虑：

```text
公网 MCP
```

例如：

```text
https://training.example.com/mcp
```

此时本地训练数据系统本身仍然不需要变化。

---

# 33. 推荐实施顺序

## Phase 1：本地数据体系

首先完成：

```text
训记 API
↓
JSON
↓
SQLite
```

重点解决：

- 数据完整保存
- 增量同步
- 数据标准化
- API 限频

---

## Phase 2：AI 分析体系

建立：

```text
AGENTS.md

reports/

analysis/

notes/
```

开始通过 IDEA AI 对数据进行：

```text
周分析
月分析
动作分析
训练讨论
```

并沉淀 Markdown。

---

## Phase 3：训练知识模型

逐渐积累：

```text
训练历史
+
分析历史
+
个人体验
+
AI 推理
```

形成：

> **个人训练模型**

重点不是简单统计：

```text
练了多少组
```

而是逐渐回答：

```text
什么样的训练量适合我？

我需要多久恢复？

什么时候提高重量？

什么时候减少训练量？

什么情况下训练效果最好？

哪些信号意味着疲劳过高？
```

---

## Phase 4：MCP

需要跨 Agent 自动调用时增加：

```text
Local MCP
```

---

## Phase 5：公网能力

真正需要云端 AI 访问时，再部署：

```text
Public MCP
```

---

# 34. 最终推荐架构

长期可以发展为：

```text
                    训记
                     │
                     ▼
                Data Sync
                     │
                     ▼
        ┌─────────────────────────┐
        │ Personal Training Data  │
        │                         │
        │ Raw JSON                │
        │ SQLite                  │
        │ Training Plans          │
        └───────────┬─────────────┘
                    │
        ┌───────────┴────────────┐
        │                        │
        ▼                        ▼
     AI Agent                  MCP
                               │
 IDEA AI                       │
 Codex                         │
 Claude Code                   │
 ChatGPT                       │
        │                      │
        └──────────┬───────────┘
                   ▼
             Analysis Layer
                   │
                   ▼
              Markdown
                   │
         ┌─────────┼──────────┐
         ▼         ▼          ▼
       周报       分析       个人知识
```

---

# 35. 核心设计思想

整个方案最重要的变化是：

原来：

> **如何让 ChatGPT 访问训记。**

现在变成：

> **如何建立属于自己的训练数据和训练知识体系，并让任何 AI Agent 都能参与分析。**

因此系统核心从：

```text
AI 平台
```

转变为：

```text
本地数据
+
本地知识
```

AI Agent 成为：

> **可替换的分析与推理工具。**

---

# 36. 最终建议

对于当前需求，暂时不建议优先开发公网 MCP Server。

最合适的第一步是：

> **建立一个本地 `training` 项目。**

首先完成：

```text
训记 API
→
本地 Raw JSON
→
SQLite
```

然后在 IDEA 中使用 AI Agent：

```text
读取训练数据
+
与用户讨论
+
分析训练变化
+
形成训练建议
+
写入 Markdown
```

逐渐形成：

```text
训练数据
+
训练历史
+
训练分析
+
个人反馈
+
长期规律
```

组成的：

> **个人训练知识库与训练决策系统。**

MCP 可以在第二阶段增加，用于：

```text
自动同步
实时查询
写回训记
跨 AI Agent 调用
```

只有真正需要云端 AI 直接访问时，才需要进一步部署公网 MCP Server。

这种“本地数据优先、AI Agent 可替换、Markdown 长期沉淀”的模式，更适合作为长期个人训练分析体系。