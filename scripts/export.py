"""Export factual weekly training summaries from SQLite.

Judgment sections (findings, next-week advice) are left for the user and agent
to fill after discussion. This script only writes numbers that come from the DB.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date, timedelta
from pathlib import Path

if __package__:
    from .db import connect_readonly
    from .paths import ROOT, DB_PATH
    from .analysis_metrics import calculate
    from .analysis_sources import training_sources
    from .analysis_report import report_text, MUSCLE_RULES, classify_movement
    from .analysis_run import load_result, store_result, verify, validate_task
else:
    from db import connect_readonly
    from paths import ROOT, DB_PATH
    from analysis_metrics import calculate
    from analysis_sources import training_sources
    from analysis_report import report_text, MUSCLE_RULES, classify_movement
    from analysis_run import load_result, store_result, verify, validate_task

# Public aliases retained for callers; one actual mapping in analysis_report.py.
MUSCLE_ORDER = ["胸", "背", "腿", "肩", "手臂", "有氧", "活动度", "未分类"]


def parse_week(token: str) -> tuple[int, int]:
    text = token.strip().upper().replace("_", "-")
    if "-W" not in text:
        raise SystemExit("week must look like 2026-W40")
    year_s, week_s = text.split("-W", 1)
    year, week = int(year_s), int(week_s)
    if week < 1 or week > 53:
        raise SystemExit(f"invalid ISO week: {token}")
    return year, week


def week_bounds(year: int, week: int) -> tuple[date, date]:
    start = date.fromisocalendar(year, week, 1)
    return start, start + timedelta(days=6)


def week_id(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def fmt_duration(seconds: int | None) -> str:
    if not seconds:
        return "0"
    minutes = int(round(seconds / 60))
    hours, mins = divmod(minutes, 60)
    if hours:
        return f"{hours}小时{mins}分"
    return f"{mins}分"


def fmt_num(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return f"{value:.1f}"


def day_status(conn: sqlite3.Connection, datestr: str) -> str:
    row = conn.execute(
        "SELECT title FROM training_session WHERE datestr = ? ORDER BY id LIMIT 1",
        (datestr,),
    ).fetchone()
    if row:
        return str(row["title"] or "训练")
    source = training_sources(conn, DB_PATH.parent / "raw", datestr, datestr)[0]
    if source["status"] == "synced_empty":
        return "休息（已同步，无训练）"
    return "无本地记录"


def collect(conn: sqlite3.Connection, start: date, end: date) -> dict:
    start_s, end_s = start.isoformat(), end.isoformat()
    sessions = conn.execute(
        """
        SELECT datestr, title, duration_s
        FROM training_session
        WHERE datestr >= ? AND datestr <= ?
        ORDER BY datestr, id
        """,
        (start_s, end_s),
    ).fetchall()

    sets = conn.execute(
        """
        SELECT s.datestr, t.movement, t.weight_kg, t.reps, t.rpe, t.done, t.is_warmup
        FROM training_set t
        JOIN training_session s ON s.id = t.session_id
        WHERE s.datestr >= ? AND s.datestr <= ?
        """,
        (start_s, end_s),
    ).fetchall()

    cardio = conn.execute(
        """
        SELECT s.datestr, c.movement, c.distance, c.kcal, c.workout_time_s, c.avg_hr
        FROM cardio_record c
        JOIN training_session s ON s.id = c.session_id
        WHERE s.datestr >= ? AND s.datestr <= ?
        """,
        (start_s, end_s),
    ).fetchall()
    return {"sessions": sessions, "sets": sets, "cardio": cardio}


def movement_stats(sets: list) -> list[dict]:
    """Legacy row-count helper only; SQL rows do not verify load or RPE level.

    New metrics and rendering use analysis_metrics.calculate instead.
    """
    bucket: dict[str, dict] = {}
    for row in sets:
        if row["is_warmup"]:
            continue
        name = row["movement"]
        item = bucket.setdefault(
            name,
            {"movement": name, "sets": 0, "done": 0, "volume": None, "max_weight": None, "rpes": [],
             "status": "unverified_semantics", "counting_unit": "normalized_row"},
        )
        item["sets"] += 1
        if row["done"]:
            item["done"] += 1
    rows = list(bucket.values())
    rows.sort(key=lambda x: (-x["done"], x["movement"]))
    return rows


def prior_max_weight(conn: sqlite3.Connection, movement: str, before: date, *, raw_root=None) -> float | None:
    """Descriptive verified external maximum; not an automatic PR baseline."""
    first = conn.execute("SELECT MIN(datestr) FROM training_session WHERE datestr < ?", (before.isoformat(),)).fetchone()[0]
    if first is None:
        return None
    end = (before-timedelta(days=1)).isoformat()
    sources = training_sources(conn, raw_root or DB_PATH.parent / "raw", first, end)
    result = calculate(conn, sources, {"start": first, "end": end, "modules": ["training"]})
    return next((m["value"] for m in result["metrics"] if m["metric_id"] == "training.max_weight" and m["group"]["movement"] == movement), None)


def week_totals(conn: sqlite3.Connection, start: date, end: date, *, raw_root=None) -> dict:
    """Return metric objects so volume states cannot be lost in a numeric tuple."""
    sources = training_sources(conn, raw_root or DB_PATH.parent / "raw", start.isoformat(), end.isoformat())
    result = calculate(conn, sources, {"start": start.isoformat(), "end": end.isoformat(), "modules": ["training"]})
    return {m["metric_id"]: m for m in result["metrics"] if m["metric_id"] in
            ("training.sessions", "training.days", "training.volume", "training.session_duration")}


def render(conn: sqlite3.Connection, year: int, week: int, *, raw_root=None) -> str:
    """Render using a private SQLite backup; never initialize the input DB."""
    start, end = week_bounds(year, week)
    temp = sqlite3.connect(":memory:")
    temp.row_factory = sqlite3.Row
    try:
        conn.backup(temp)
        task = validate_task({"start": start.isoformat(), "end": end.isoformat(),
                              "modules": ["training"], "questions": ["training.recorded_counts"]})
        sources = training_sources(temp, raw_root or DB_PATH.parent / "raw",
                                   (start-timedelta(weeks=4)).isoformat(), end.isoformat())
        result = calculate(temp, sources, task)
        store_result(temp, result)
        # Render the derived facts read back from SQLite, not a parallel result.
        return report_text(temp, load_result(temp), sources)
    finally:
        temp.close()


def report_path(year: int, week: int) -> Path:
    return ROOT / "reports" / "weekly" / f"{year}-W{week:02d}.md"


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a factual ISO-week training report")
    parser.add_argument("--week", required=True, help="ISO week, e.g. 2026-W40")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--db", type=Path, default=None, help="Existing read-only SQLite input")
    source.add_argument("--run", type=Path, help="Verified frozen analysis run")
    parser.add_argument("--raw-root", type=Path, help="Raw directory for --db (not used with --run)")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write reports/weekly/YYYY-Www.md",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing weekly report",
    )
    args = parser.parse_args()
    year, week = parse_week(args.week)
    if args.run:
        if args.raw_root:
            parser.error("--raw-root cannot be combined with --run")
        manifest = verify(args.run)
        start, end = week_bounds(year, week)
        if (manifest["task"]["start"], manifest["task"]["end"]) != (start.isoformat(), end.isoformat()):
            parser.error("--week must match the frozen task window")
        if "report.md" not in manifest["files"]:
            parser.error("Frozen run has no training report")
        text = (args.run / "report.md").read_text(encoding="utf-8")
    else:
        db_path = args.db or DB_PATH
        conn = connect_readonly(db_path)
        try:
            text = render(conn, year, week, raw_root=args.raw_root or db_path.parent / "raw")
        finally:
            conn.close()

    if args.write:
        path = report_path(year, week)
        if path.exists() and not args.force:
            raise SystemExit(f"exists: {path} (pass --force to overwrite)")
        if path.exists():
            text = preserve_discussion(path.read_text(encoding="utf-8"), text)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    else:
        print(text)


def preserve_discussion(old: str, new: str) -> str:
    """Force refresh updates facts, never discards confirmed discussion sections."""
    import re
    headings = ("六、恢复情况", "八、主要发现", "九、下周建议", "十、需要继续观察的问题")
    for heading in headings:
        pattern = rf"(?ms)^## {re.escape(heading)}\s*\n.*?(?=^## |\Z)"
        match = re.search(pattern, old)
        if match is None or re.search(pattern, new) is None:
            raise ValueError("Cannot safely preserve report discussions; use a separate output")
        new = re.sub(pattern, lambda _: match.group(0), new, count=1)
    return new


if __name__ == "__main__":
    main()
