# 训记数据访问层

## 范围与安全边界

- 统一入口：`scripts/access.py` 的 `DataAccess`；命令入口：`scripts/data_access.py`。
- **默认只读本地，不访问网络**。缓存缺失会报错，不会偷偷拉取；CLI 加 `--sync` 或 Python 显式设置 `allow_remote=True` 才允许读取 API。
- 四个域使用独立环境变量：训练 `XUNJI_API_KEY`、饮食 `XUNJI_FOOD_API_KEY`、身体 `XUNJI_BODY_API_KEY`、个人模板 `XUNJI_TEMPLATE_API_KEY`。缺少域密钥时不回退到训练密钥。
- 一个本地 `data/` 对应一个训记用户。切换账号应使用独立 `data_dir`，不能混用旧用户缓存。
- 新增入口均为读取，不提供身体、饮食或模板写入命令；原有训练写回封装增加显式 `confirmed=True` 防误调用。确认标志不能代替向用户展示摘要并取得确认。
- **个人模板只包含「Agent 模板」专属文件夹**，不等于 App 中的所有个人模板。饮食模板是另一个独立接口。

## 命令行

以下命令在项目根目录、已激活 `.venv` 后执行；也可将 `python` 换成 `.\.venv\Scripts\python.exe`。

```powershell
# 本地查询：首次使用时自动导入匹配的旧格式归档，无需密钥
python scripts/data_access.py training --date 2026-11-04 --json
python scripts/data_access.py food --start 2026-11-04 --end 2026-11-04 --json
python scripts/data_access.py food-templates --json

# 显式允许远程读取；已经缓存的相同查询仍优先使用本地
python scripts/data_access.py body --start 2026-10-01 --end 2026-11-06 --types weight bodyfat --sync --json

# 确实需要刷新时，追加新快照，不覆盖已有原始文件
python scripts/data_access.py food --start 2026-11-05 --end 2026-11-05 --sync --refresh

# Agent 文件夹：首次同步从 cursor=0 开始，以后自动从持久游标续传
python scripts/data_access.py templates --sync
python scripts/data_access.py templates --json

# 仅首次同步时选用：不读取模板训练内容
python scripts/data_access.py templates --sync --metadata-only

# 不联网，从旧归档和新快照重新建立 SQLite 索引/模板游标
python scripts/data_access.py reindex
python scripts/data_access.py reindex --domain food
```

`--json` 输出完整数据；默认仅输出数量及游标。`--refresh` 必须配合 `--sync`，模板不支持 refresh，以防无意重置增量游标。

### 原有同步脚本

```powershell
# 默认续接上一次同步，重叠最近 3 天；状态在每一天成功后原子保存
python scripts/sync_training.py
python scripts/sync_training.py --days 7 --refresh-recent 0
python scripts/sync_training.py --start 2026-11-01 --end 2026-11-06

# 饮食同步现在同时入 SQLite；默认过去一年至今天，推荐指定实际需要的范围
python scripts/sync_food.py --start 2026-11-01 --end 2026-11-06
python scripts/sync_food.py --start 2026-11-05 --end 2026-11-05 --refresh
```

`sync_training.py` / `sync_food.py` 本身就是显式同步命令，会允许远程读取。训练 `--refresh-recent` 只作用于所选范围的末尾 N 天；旧 Raw 文件始终保留。`normalize.py --all` 仍只重建训练数据，且会按时间重放新增的训练快照。

## Python

```python
from scripts.access import DataAccess

with DataAccess() as data:  # 不需要 API Key，不联网
    day = data.get_food("2026-11-04", "2026-11-04")
    training = data.get_training("2026-11-04")

with DataAccess(allow_remote=True) as data:
    body = data.get_body("2026-10-01", "2026-11-06", types=["weight"])
    templates = data.sync_templates()
```

日期读取方法返回 API 形状的字典。身体数据返回合并后的分页视图，饮食返回已覆盖日期范围的最新本地日数据；两者不冒充某一次原始响应。`get_templates()` 返回 `{cursor, folder, limits, include_content, templates, ...}` 本地物化视图，不会主动同步。

`data.repository.food_days(start, end)`、`body_records(start, end, types)` 可查询已入库记录，但**返回空列表不代表该范围已同步完整**；需要完整性判断时应使用上层读取方法。可通过 `DataAccess(data_dir=Path(...))` 隔离测试数据或不同账号。

## 持久化

沿用 `data/training.db`，schema v2 以新增表兼容已有训练库；遇到更高版本拒绝降级。不会改动原有训练表结构。

| 表 | 用途 |
|---|---|
| 原有 `training_session` / `training_set` 等 | 训练、组、有氧、心率 |
| `food_day` | 日期、模板 ID、实际摄入汇总、当日宏量目标、完整日 JSON |
| `food_record` | 每项食物、餐次、数量、单位、完整营养与扩展字段 |
| `body_record` | 按 `datestr + type` 保存指标，保留单位及完整 JSON |
| `agent_template` | 模板内容与删除 tombstone |
| `access_cache` | 查询条件到原始快照的索引 |
| `access_state` | 模板 cursor、folder、limits 等增量状态 |

食物 `record_id` **不是跨日期全局唯一键**；本地以日期和记录顺序保存整日快照，不根据 ID 去重丢数据。未返回的目标保留为空，不把饮水空记录当作没喝水，也不推算 TDEE 或高低碳日名称。

每次远程读取追加到 `data/raw/<domain>/snapshots/`：

- `*.json`：原始 API 形状响应；不覆盖旧文件。
- `*.request.json`：对应的域与无密钥查询条件，供离线重放。

旧的按日训练文件、饮食日期范围文件及 `food/templates/list.json` 均可原地读取，不迁移、不修改。`reindex` 先读旧格式，再按时间重放新快照；模板页已应用时不倒退游标。重建无需删除数据库；缺失、损坏或不认识的记录会报错，不能把部分恢复声称为完整恢复。

## API 约束与故障处理

- gzip 自动解压并兼容缺少压缩头的响应；HTTP 200 也必须验证业务结果，不能将鉴权失败当成空记录。
- 读取遇网络错误、429 或临时 5xx 时最多尝试 3 次。正确区分 `retry_after_ms` 与秒，同时尊重 `Retry-After`；鉴权、会员权限等非临时错误不重试。
- 同一进程同 Key 的客户端实例共享限频；不同进程仍可能竞争服务端额度，服务端限频响应会触发等待。
- 错误只暴露安全分类，不输出响应体、请求头、Key 或底层 HTTP 异常。底层 `XunjiApiError.code` 可用于判断 `auth_error`、`cursor_invalid` 等。
- 饮食远程查询限过去一年到未来约 3 个月（按 90 天校验）；离线旧归档不受移动日期窗口限制。默认完整读取，保留 `limits`、营养、饮水、模板 ID 等字段。
- 身体查询每页最多 500 条，自动翻页、检测重复/不前进页面，并设最大页数保护。腰围沿用官方历史拼写 **`weist`**。`latest` 保留接口原义，不能假设它一定在请求日期范围内。
- 身体分页不是服务端一致性快照；同步期间数据变更可能导致重复页，遇此情况报错并提示显式 refresh，不静默丢项。
- 模板每页固定 `limit=15`。按 revision 顺序应用 changes，内容、删除标记与 cursor 同事务提交；后续缺省的 folder/limits 不清空。文件夹删除使本地模板全部标记删除，绝不发送重建请求。
- 真实模板同步响应为顶层 `schema_version: agent_personal_templates_v1`、`from_cursor`、`next_cursor`、`changes` 等字段，变更使用 `entity_type` / `entity_id` / `operation`。访问层兼容此结构与旧的 `res` 包装；原始归档不为适配而改写。返回的 `from_cursor` 必须匹配请求。
- 模板同步的 `include_content` 模式首次确定后保持不变，避免把元数据缓存误当成完整内容；不自动切换模式或重置 cursor。服务端报 `cursor invalid` 时保留现场并报错，不擅自重新全量同步。
- 模板未知的 change 类型/操作会保留原始快照、回滚本页、停止推进游标，便于补充真实响应契约后重放。

## 验证

```powershell
python -m unittest discover -s tests -v
```

测试只使用 MockTransport、合成数据和临时 SQLite，不读取真实 Key、不调用训记。现有本地训练/饮食归档还通过临时副本做了兼容性检查。2026-11-06 已完成身体查询、Agent 空文件夹首屏同步的真实 API 联调，并据此补充顶层模板响应的回归测试；非空模板内容、真实删除事件和多页场景仍由离线测试覆盖。实际返回结构有差异时应安全失败，不能猜字段推进游标。
