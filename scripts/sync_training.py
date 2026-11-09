"""Incremental training sync using local-first access and immutable snapshots."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone

if __package__:
    from .access import DataAccess
    from .paths import SYNC_STATE_PATH
    from .storage import atomic_json
    from .xunji_client import XunjiApiError
else:
    from access import DataAccess
    from paths import SYNC_STATE_PATH
    from storage import atomic_json
    from xunji_client import XunjiApiError


def _date_range(start: date, end: date) -> list[str]:
    if end < start:
        raise ValueError("--end must be >= --start")
    return [(start + timedelta(days=n)).isoformat() for n in range((end - start).days + 1)]


def _load_state() -> dict:
    return json.loads(SYNC_STATE_PATH.read_text(encoding="utf-8")) if SYNC_STATE_PATH.exists() else {}


def sync(*, days: int | None = None, start: str | None = None, end: str | None = None,
         refresh_recent: int = 3) -> None:
    if refresh_recent < 0 or (days is not None and days < 1):
        raise ValueError("--days must be >= 1 and --refresh-recent must be >= 0")
    if days is not None and (start or end):
        raise ValueError("Use --days or --start/--end, not both")
    today, state = date.today(), _load_state()
    end_d = date.fromisoformat(end) if end else today
    if start:
        start_d = date.fromisoformat(start)
    elif days is not None or end:
        start_d = end_d - timedelta(days=(days or 7) - 1)
    elif state.get("last_training_date"):
        last = date.fromisoformat(state["last_training_date"])
        start_d = min(today, last - timedelta(days=max(refresh_recent - 1, 0)))
    else:
        start_d = today - timedelta(days=6)
    dates = _date_range(start_d, end_d)
    refresh_set = set(dates[-refresh_recent:]) if refresh_recent else set()
    fetched = skipped = sessions = 0
    with DataAccess(allow_remote=True) as data:
        for datestr in dates:
            query = {"datestr": datestr, "include_full_data": True}
            legacy = data.raw_dir / "training" / datestr[:4] / f"{datestr}.json"
            cached = data.repository.cached("training", query) is not None or legacy.is_file()
            refresh = datestr in refresh_set
            payload = data.get_training(datestr, refresh=refresh)
            trains = payload["res"].get("trains", []) if isinstance(payload["res"], dict) else payload["res"]
            sessions += len(trains)
            if cached and not refresh:
                skipped += 1
                print(f"[skip] {datestr} (cached)")
            else:
                fetched += 1
                print(f"[fetched] {datestr} sessions={len(trains)}")
            # A failed later request cannot lose already completed progress.
            state.update(last_sync=datetime.now(timezone.utc).isoformat(),
                         last_training_date=max(state.get("last_training_date", datestr), datestr),
                         last_range={"start": dates[0], "end": datestr},
                         last_fetched=fetched, last_skipped=skipped)
            atomic_json(SYNC_STATE_PATH, state)
    print(f"done fetched={fetched} skipped={skipped} normalized_sessions={sessions}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Incremental training sync to Raw + SQLite")
    parser.add_argument("--days", type=int, help="Last N days (default: resume previous sync)")
    parser.add_argument("--start", help="YYYY-MM-DD")
    parser.add_argument("--end", help="YYYY-MM-DD")
    parser.add_argument("--refresh-recent", type=int, default=3, help="Fetch new snapshots for last N days (default 3)")
    args = parser.parse_args()
    try:
        sync(days=args.days, start=args.start, end=args.end, refresh_recent=args.refresh_recent)
    except (XunjiApiError, ValueError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
