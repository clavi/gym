"""Explicit food sync, immutable archives and SQLite normalization."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

if __package__:
    from .access import DataAccess
    from .xunji_client import XunjiApiError, XunjiFoodClient, previous_year
else:
    from access import DataAccess
    from xunji_client import XunjiApiError, XunjiFoodClient, previous_year

# Retain the helper/client imports used by earlier local scripts.
earliest_date = previous_year


def sync(start: str | None = None, end: str | None = None, *, refresh: bool = False) -> Path:
    today = date.today()
    start, end = start or previous_year(today).isoformat(), end or today.isoformat()
    with DataAccess(allow_remote=True) as data:
        payload = data.get_food(start, end, refresh=refresh)
        query = {"start_date": start, "end_date": end, "include_detail": True}
        cached = data.repository.cached("food", query) or data.repository.covering_food(start, end)
        path = cached[1]
        days = payload["res"]["days"]
        count = sum(len(day.get("foods", {}).get("records", [])) for day in days)
        print(f"food {start}..{end} days={len(days)} records={count} raw={path}")
        return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync detailed food records to Raw + SQLite")
    parser.add_argument("--start", help="YYYY-MM-DD (default one year ago)")
    parser.add_argument("--end", help="YYYY-MM-DD (default today)")
    parser.add_argument("--refresh", action="store_true", help="Fetch a new immutable snapshot")
    args = parser.parse_args()
    try:
        sync(args.start, args.end, refresh=args.refresh)
    except (XunjiApiError, ValueError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
