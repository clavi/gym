"""Factual Markdown rendering for analysis-metrics-v2.1 (no judgments)."""
from __future__ import annotations

from datetime import date, timedelta

if __package__:
    from .analysis_metrics import calculate
else:
    from analysis_metrics import calculate

MUSCLE_RULES = [
    ("活动度", ("拉伸", "热身")),
    ("肩", ("反向飞鸟", "面拉", "耸肩", "平举", "Y字", "W字", "侧转", "侧拉")),
    ("胸", ("卧推", "推胸", "胸部", "飞鸟")),
    ("背", ("划船", "下拉", "引体")),
    ("腿", ("深蹲", "腿举", "腿弯", "髋内收", "髋外展", "后屈伸")),
    ("手臂", ("弯举", "臂屈伸", "下压", "三头", "二头")),
    ("肩", ("推举",)), ("有氧", ("爬坡", "跑步", "步行", "单车", "椭圆")),
]


def classify_movement(name):
    return next((g for g, words in MUSCLE_RULES if any(w in name for w in words)), "未分类")


def fmt_num(value):
    return "—" if value is None else f"{value:g}"


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def metric(result, mid):
    return next(m for m in result["metrics"] if m["metric_id"] == mid)


def week_coverage(result):
    task = result["task"]
    start, end, today = (date.fromisoformat(task[k]) for k in ("start", "end", "as_of"))
    calendar = (end-start).days+1
    elapsed = max(0, min(calendar, (today-start).days+1))
    covered = sum(d.get("training_sync") in ("synced_records", "synced_empty") for d in result["daily"])
    confirmations = task.get("recording_completeness", {}).get("training", {})
    complete = all(confirmations.get(d["date"], {}).get("state") == "complete"
                   and confirmations.get(d["date"], {}).get("evidence") for d in result["daily"])
    return calendar, elapsed, covered, complete


def report_text(conn, result, sources, semantic_rules=None):
    task = result["task"]
    start, end = date.fromisoformat(task["start"]), date.fromisoformat(task["end"])
    iso = start.isocalendar()
    label = f"{iso.year}-W{iso.week:02d}"
    count = metric(result, "training.sessions")
    days = metric(result, "training.days")
    rows = metric(result, "training.completed_segments")
    groups = metric(result, "training.work_sets")
    volume = metric(result, "training.volume")
    duration = metric(result, "training.session_duration")
    rpe = metric(result, "training.rpe_mean")
    rpe_cov = metric(result, "training.rpe_coverage")
    high = metric(result, "training.high_rpe_share")
    calendar, elapsed, covered, complete = week_coverage(result)
    lines = [f"# {label} 训练总结", "",
             "> 第一至五节、第七节由 `scripts/export.py` 从只读一致库快照及其中派生事实生成。",
             "> 口径 `analysis-metrics-v2.1`；第六、八、九、十节待用户讨论确认。", "",
             f"区间：{start} ~ {end}；截至 {task['as_of']}。", "",
             "## 一、本周概况", "", f"训练次数：{count['value']}；训练天数：{days['value']}。", "",
             f"日历/已过/已同步覆盖天数：{calendar}/{elapsed}/{covered}；记录完整性：{'用户确认完整' if complete else '未知或未完整确认'}。", "",
             f"完成记录段：{rows['value']}（normalized_row，不等于逻辑正式组）。", "",
             f"逻辑正式组：{fmt_num(groups['value'])}；状态 {groups['status']}；已核实 {groups['verified_group_n']}。", "",
             f"可计算容量：{fmt_num(volume['value'])} kg·次；状态 `{volume['status']}`。", "",
             f"可算段/候选段：{volume['numerator']}/{fmt_num(volume['denominator'])}；原因 {volume['reasons']}。", "",
             f"记录时长之和：{fmt_num(duration['value'])} 秒（有效记录 {duration['sample_n']}；状态 {duration['status']}，未消除时段重叠）。", "",
             "## 二、训练安排", "", "| 日期 | 记录 | 同步覆盖 | 容量 | 容量状态 |", "|---|---|---|---|---|"]
    if elapsed < calendar:
        lines.insert(4, "> 本 ISO 周尚未结束（部分周）；不得与完整周直接比较总量优劣。")
    for d in result["daily"]:
        title = d["training_titles"] if d["training_sessions"] else ("休息（已同步，无训练）" if d["training_sync"] == "synced_empty" else "覆盖未知/来源待核对")
        lines.append(f"| {d['date']} | {cell(title)} | {d['training_sync']} | {fmt_num(d['volume'])} | {d['volume_status']} |")
    lines.extend(["", "## 三、各肌群训练量", "", "关键词归类仅为启发式；下列数目为库中完成记录段，不是有效增肌组。", "",
                  "| 动作 | 启发式肌群 | 完成记录段 |", "|---|---|---|"])
    for row in conn.execute("SELECT t.movement,COUNT(*) n FROM training_set t JOIN training_session s ON s.id=t.session_id "
                            "WHERE s.datestr BETWEEN ? AND ? AND t.done=1 AND t.is_warmup=0 GROUP BY t.movement ORDER BY t.movement",
                            (task["start"], task["end"])):
        lines.append(f"| {cell(row['movement'])} | {classify_movement(row['movement'])} | {row['n']} |")
    cardio = conn.execute("SELECT COUNT(*) FROM cardio_record c JOIN training_session s ON s.id=c.session_id WHERE s.datestr BETWEEN ? AND ?",
                          (task["start"], task["end"])).fetchone()[0]
    lines.extend(["", f"有氧记录：{cardio} 条；单位/设备未登记前不跨记录累计距离或热量。", "",
                  "## 四、主要动作变化", "", "只列本期独立完成非热身段的正外部重量；未登记器械/左右及基线条件，不判断进退步或 PR。", ""])
    for m in result["metrics"]:
        if m["metric_id"] == "training.max_weight":
            lines.append(f"- {cell(m['group']['movement'])}：最大外部重量 {fmt_num(m['value'])} kg，样本 {m['sample_n']}；{m['status']}（含独立递减段时仍为段级记录）。")
    lines.extend(["", "## 五、训练强度", "",
                  f"段级平均 RPE：{fmt_num(rpe['value']) if rpe['value'] is not None else '无有效独立记录'}；独立样本 {rpe['sample_n']}。", "",
                  f"段级 RPE 覆盖：{rpe_cov['numerator']}/{fmt_num(rpe_cov['denominator'])}，比率 {fmt_num(rpe_cov['value'])}；父组共享评分不复制。", "",
                  f"高 RPE（≥9）独立评分段：{high['numerator']}/{high['denominator']}，比例 {fmt_num(high['value'])}。", "",
                  "## 六、恢复情况", "", "待讨论。", "", "## 七、与前4周比较", "",
                  "仅描述已记录数量；记录完整性、覆盖或可比口径不满足时，不比较完整周总量优劣。", "",
                  "| 周 | 次数 | 天数 | 容量 | 状态 | 日历/已过/覆盖天 | 记录完整性 | 次数比较资格 | 容量比较资格 |", "|---|---|---|---|---|---|---|---|---|"])
    for offset in range(4, -1, -1):
        ws = start - timedelta(weeks=offset)
        we = ws + timedelta(days=6)
        prev_task = {**task, "start": ws.isoformat(), "end": we.isoformat(), "modules": ["training"]}
        # Current-task confirmations/mappings do not establish historical applicability.
        previous = result if offset == 0 else calculate(conn, sources, prev_task)
        # Missing frozen evidence for a previous week is unknown, never no-training.
        pv = metric(previous, "training.volume")
        c, e, s, r = week_coverage(previous)
        count_eligible = c == e == s == 7 and r
        eligible = count_eligible and pv["status"] in ("complete", "no_training", "no_completed_work")
        wi = ws.isocalendar()
        lines.append(f"| {wi.year}-W{wi.week:02d} | {metric(previous, 'training.sessions')['value']} | {metric(previous, 'training.days')['value']} | "
                     f"{fmt_num(pv['value'])} | {pv['status']} | {c}/{e}/{s} | {'确认完整' if r else '未知/不完整'} | "
                     f"{'具备次数覆盖条件' if count_eligible else '仅描述'} | {'可比较，仍需同口径' if eligible else '仅描述'} |")
    for title in ("八、主要发现", "九、下周建议", "十、需要继续观察的问题"):
        lines.extend(["", f"## {title}", "", "待讨论。"])
    return "\n".join(lines) + "\n"
