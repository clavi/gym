"""Flatten raw training JSON into SQLite set-level rows."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from db import connect, delete_sessions_for_date, init_db
from paths import RAW_TRAINING_DIR, raw_training_path


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        return float(m.group(0)) if m else None


def _to_bool_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value != 0)
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "y", "done"):
        return 1
    if s in ("false", "0", "no", "n"):
        return 0
    return None


def _weight_kg(set_obj: dict[str, Any]) -> float | None:
    if "weight_kg" in set_obj and set_obj["weight_kg"] not in (None, ""):
        return _to_float(set_obj["weight_kg"])
    w = _to_float(set_obj.get("weight"))
    if w is None:
        return None
    unit = str(set_obj.get("unit") or "kg").lower()
    if unit in ("lb", "lbs"):
        return w * 0.45359237
    return w


def _extract_trains(payload: dict[str, Any]) -> list[dict[str, Any]]:
    res = payload.get("res")
    if res is None:
        return []
    if isinstance(res, list):
        return [t for t in res if isinstance(t, dict)]
    if isinstance(res, dict):
        trains = res.get("trains")
        if isinstance(trains, list):
            return [t for t in trains if isinstance(t, dict)]
        # Single train object
        if "movements" in res or "localid" in res:
            return [res]
    return []


def _hr_fields(hr: dict[str, Any]) -> tuple[float | None, float | None, float | None, float | None, str | None]:
    return (
        _to_float(hr.get("avg")),
        _to_float(hr.get("max")),
        _to_float(hr.get("min")),
        _to_float(hr.get("duration")),
        json.dumps(hr.get("values"), ensure_ascii=False) if hr.get("values") is not None else None,
    )


def _insert_set(
    conn,
    session_id: int,
    *,
    movement: str,
    set_index: int,
    set_obj: dict[str, Any],
    set_type: str | None = None,
    parent_movement: str | None = None,
) -> None:
    st = set_type or set_obj.get("setType")
    is_warmup = 1 if st == "热" else 0
    conn.execute(
        """
        INSERT INTO training_set (
            session_id, movement, set_index, set_type, weight_kg, reps, rpe,
            done, rest_seconds, is_warmup, parent_movement
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            movement,
            set_index,
            st,
            _weight_kg(set_obj),
            _to_float(set_obj.get("reps")),
            _to_float(set_obj.get("rpe")),
            _to_bool_int(set_obj.get("done")),
            _to_float(set_obj.get("restSeconds")),
            is_warmup,
            parent_movement,
        ),
    )


def _normalize_movement(
    conn,
    session_id: int,
    movement: dict[str, Any],
) -> None:
    name = str(movement.get("name") or "未知动作")

    # Native cardio day / cardio movement without sets
    if movement.get("cardio") is True or (
        not movement.get("sets") and movement.get("metrics")
    ):
        metrics = movement.get("metrics") or {}
        if isinstance(metrics, dict) and (
            movement.get("cardio")
            or any(k in metrics for k in ("distance", "kcal", "calories", "workoutTime", "pace"))
        ):
            conn.execute(
                """
                INSERT INTO cardio_record (
                    session_id, movement, distance, kcal, workout_time_s,
                    avg_hr, max_hr, min_hr, pace
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    name,
                    _to_float(metrics.get("distance")),
                    _to_float(metrics.get("kcal") or metrics.get("calories")),
                    _to_float(metrics.get("workoutTime") or metrics.get("duration_s")),
                    _to_float(metrics.get("avgHeartRate") or metrics.get("bpm")),
                    _to_float(metrics.get("maxHeartRate")),
                    _to_float(metrics.get("minHeartRate")),
                    str(metrics["pace"]) if metrics.get("pace") is not None else None,
                ),
            )
            if movement.get("cardio") and not movement.get("sets"):
                return

    sets = movement.get("sets") or []
    if not isinstance(sets, list):
        return

    for idx, set_obj in enumerate(sets):
        if not isinstance(set_obj, dict):
            continue

        # Superset round: items[]
        items = set_obj.get("items")
        if isinstance(items, list) and items:
            for item in items:
                if not isinstance(item, dict):
                    continue
                child_name = str(item.get("name") or name)
                child_set = item.get("set") if isinstance(item.get("set"), dict) else item
                if not isinstance(child_set, dict):
                    continue
                _insert_set(
                    conn,
                    session_id,
                    movement=child_name,
                    set_index=idx,
                    set_obj=child_set,
                    set_type=child_set.get("setType") or set_obj.get("setType"),
                    parent_movement=name,
                )
            continue

        _insert_set(
            conn,
            session_id,
            movement=name,
            set_index=idx,
            set_obj=set_obj,
        )

        # Drop sets
        drop_sets = set_obj.get("dropSets") or []
        if isinstance(drop_sets, list):
            for drop in drop_sets:
                if not isinstance(drop, dict):
                    continue
                _insert_set(
                    conn,
                    session_id,
                    movement=name,
                    set_index=idx,
                    set_obj=drop,
                    set_type="drop",
                    parent_movement=None,
                )

        # Set-level heart rate
        hr = set_obj.get("heartRate")
        if isinstance(hr, dict):
            avg, mx, mn, dur, values_json = _hr_fields(hr)
            conn.execute(
                """
                INSERT INTO heart_rate_summary (
                    session_id, scope, movement, set_index, avg, max, min, duration, values_json
                ) VALUES (?, 'set', ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, name, idx, avg, mx, mn, dur, values_json),
            )

        metrics = set_obj.get("metrics")
        if isinstance(metrics, dict) and any(
            k in metrics for k in ("distance", "kcal", "calories", "workoutTime", "avgHeartRate")
        ):
            conn.execute(
                """
                INSERT INTO cardio_record (
                    session_id, movement, distance, kcal, workout_time_s,
                    avg_hr, max_hr, min_hr, pace
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    name,
                    _to_float(metrics.get("distance")),
                    _to_float(metrics.get("kcal") or metrics.get("calories")),
                    _to_float(metrics.get("workoutTime") or metrics.get("duration_s")),
                    _to_float(metrics.get("avgHeartRate")),
                    _to_float(metrics.get("maxHeartRate")),
                    _to_float(metrics.get("minHeartRate")),
                    str(metrics["pace"]) if metrics.get("pace") is not None else None,
                ),
            )


def normalize_payload(conn, payload: dict[str, Any], datestr: str | None = None) -> int:
    """Replace DB rows for dates found in payload. Returns session count."""
    trains = _extract_trains(payload)
    dates: set[str] = set()
    for train in trains:
        d = train.get("datestr") or datestr
        if d:
            dates.add(str(d))
    if datestr:
        dates.add(datestr)
    if not dates and datestr:
        dates.add(datestr)

    for d in dates:
        delete_sessions_for_date(conn, d)

    count = 0
    for train in trains:
        d = str(train.get("datestr") or datestr or "")
        if not d:
            continue
        localid = train.get("localid")
        start_ms = train.get("start")
        end_ms = train.get("end")
        duration_s = None
        if isinstance(start_ms, (int, float)) and isinstance(end_ms, (int, float)):
            duration_s = int((end_ms - start_ms) / 1000)

        cur = conn.execute(
            """
            INSERT INTO training_session (datestr, localid, title, start_ms, end_ms, duration_s)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                d,
                int(localid) if localid is not None else None,
                train.get("title") or train.get("name"),
                int(start_ms) if isinstance(start_ms, (int, float)) else None,
                int(end_ms) if isinstance(end_ms, (int, float)) else None,
                duration_s,
            ),
        )
        session_id = cur.lastrowid
        count += 1

        # Session-level heart rate
        hr = train.get("heartRate")
        if hr is None and isinstance(train.get("note"), dict):
            hr = train["note"].get("heartRate")
        if isinstance(hr, dict):
            avg, mx, mn, dur, values_json = _hr_fields(hr)
            conn.execute(
                """
                INSERT INTO heart_rate_summary (
                    session_id, scope, movement, set_index, avg, max, min, duration, values_json
                ) VALUES (?, 'session', NULL, NULL, ?, ?, ?, ?, ?)
                """,
                (session_id, avg, mx, mn, dur, values_json),
            )

        movements = train.get("movements") or []
        if isinstance(movements, list):
            for movement in movements:
                if isinstance(movement, dict):
                    _normalize_movement(conn, session_id, movement)

    # Empty day: still clear old data (already deleted); nothing to insert
    if not trains and datestr:
        delete_sessions_for_date(conn, datestr)

    conn.commit()
    return count


def normalize_file(path: Path, conn=None) -> int:
    own = conn is None
    conn = conn or init_db()
    payload = json.loads(path.read_text(encoding="utf-8"))
    datestr = path.stem  # YYYY-MM-DD
    n = normalize_payload(conn, payload, datestr=datestr)
    if own:
        conn.close()
    return n


def normalize_date(datestr: str, conn=None) -> int:
    path = raw_training_path(datestr)
    if not path.exists():
        raise FileNotFoundError(f"Raw file not found: {path}")
    return normalize_file(path, conn=conn)


def normalize_all(conn=None) -> tuple[int, int]:
    own = conn is None
    conn = conn or init_db()
    files = sorted(RAW_TRAINING_DIR.glob("*/*.json"))
    total_sessions = 0
    for path in files:
        total_sessions += normalize_file(path, conn=conn)
    if own:
        conn.close()
    return len(files), total_sessions


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize raw training JSON into SQLite")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", help="YYYY-MM-DD")
    group.add_argument("--all", action="store_true", help="Normalize all raw files")
    args = parser.parse_args()

    conn = init_db()
    try:
        if args.all:
            n_files, n_sessions = normalize_all(conn)
            print(f"normalized files={n_files} sessions={n_sessions}")
            print(f"sessions_total={conn.execute('SELECT COUNT(*) FROM training_session').fetchone()[0]}")
            print(f"sets_total={conn.execute('SELECT COUNT(*) FROM training_set').fetchone()[0]}")
        else:
            n = normalize_date(args.date, conn)
            print(f"normalized date={args.date} sessions={n}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
