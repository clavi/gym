"""Incrementally sync training days from 训记 API to Raw JSON + SQLite."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from db import init_db
from normalize import normalize_payload
from paths import SYNC_STATE_PATH, ensure_data_dirs, raw_training_path
from xunji_client import XunjiClient


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _date_range(start: date, end: date) -> list[str]:
    if end < start:
        raise SystemExit("--end must be >= --start")
    days = []
    cur = start
    while cur <= end:
        days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


def _load_state() -> dict:
    if SYNC_STATE_PATH.exists():
        return json.loads(SYNC_STATE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_state(state: dict) -> None:
    ensure_data_dirs()
    SYNC_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_raw(datestr: str, payload: dict) -> Path:
    path = raw_training_path(datestr)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def sync(
    *,
    days: int | None = None,
    start: str | None = None,
    end: str | None = None,
    refresh_recent: int = 3,
) -> None:
    ensure_data_dirs()
    today = date.today()

    if start or end:
        start_d = _parse_date(start) if start else today
        end_d = _parse_date(end) if end else today
        dates = _date_range(start_d, end_d)
    else:
        n = days if days is not None else 7
        if n < 1:
            raise SystemExit("--days must be >= 1")
        start_d = today - timedelta(days=n - 1)
        dates = _date_range(start_d, today)

    refresh_set = set(dates[-refresh_recent:]) if refresh_recent > 0 else set()

    conn = init_db()
    fetched = 0
    skipped = 0
    sessions = 0

    with XunjiClient() as client:
        for datestr in dates:
            path = raw_training_path(datestr)
            force = datestr in refresh_set
            if path.exists() and not force:
                print(f"[skip] {datestr} (cached)")
                skipped += 1
                # Ensure DB has this day even if only raw exists
                payload = json.loads(path.read_text(encoding="utf-8"))
                sessions += normalize_payload(conn, payload, datestr=datestr)
                continue

            print(f"[fetch] {datestr} full=true")
            payload = client.get_training(datestr, include_full_data=True)
            _write_raw(datestr, payload)
            n = normalize_payload(conn, payload, datestr=datestr)
            sessions += n
            fetched += 1
            print(f"  -> wrote {path} sessions={n}")

    state = _load_state()
    state.update(
        {
            "last_sync": datetime.now(timezone.utc).isoformat(),
            "last_training_date": dates[-1],
            "last_range": {"start": dates[0], "end": dates[-1]},
            "last_fetched": fetched,
            "last_skipped": skipped,
        }
    )
    _save_state(state)
    conn.close()
    print(
        f"done fetched={fetched} skipped={skipped} "
        f"normalized_sessions={sessions} state={SYNC_STATE_PATH}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync 训记 training data to local Raw + SQLite")
    parser.add_argument("--days", type=int, default=None, help="Sync last N days including today (default 7)")
    parser.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD")
    parser.add_argument(
        "--refresh-recent",
        type=int,
        default=3,
        help="Force re-fetch the most recent N days in range (default 3)",
    )
    args = parser.parse_args()

    if args.start or args.end:
        sync(start=args.start, end=args.end, refresh_recent=args.refresh_recent)
    else:
        sync(days=args.days or 7, refresh_recent=args.refresh_recent)


if __name__ == "__main__":
    main()
