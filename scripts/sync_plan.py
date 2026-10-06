"""Sync official training plans from 训记 API to data/plans/."""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from paths import PLANS_DIR, ensure_data_dirs, plan_path
from xunji_client import XunjiClient


def _clamp_range(start: date, end: date) -> tuple[date, date]:
    if end < start:
        raise SystemExit("--end must be >= --start")
    # API custom range max 92 days inclusive
    max_span = 91
    if (end - start).days > max_span:
        end = start + timedelta(days=max_span)
        print(f"[warn] range truncated to 92 days; end={end.isoformat()}")
    return start, end


def sync_plans(
    *,
    start: str | None = None,
    end: str | None = None,
) -> None:
    ensure_data_dirs()
    today = date.today()
    start_d = date.fromisoformat(start) if start else today - timedelta(days=7)
    end_d = date.fromisoformat(end) if end else today + timedelta(days=30)
    start_d, end_d = _clamp_range(start_d, end_d)

    with XunjiClient() as client:
        print("[fetch] plan list")
        listed = client.list_plans()
        index_path = PLANS_DIR / "index.json"
        index_path.write_text(
            json.dumps(listed, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"  -> {index_path}")

        res = listed.get("res") or {}
        plans = []
        if isinstance(res, dict):
            plans = res.get("plans") or []
        elif isinstance(res, list):
            plans = res

        if not plans:
            print("no plans returned")
            return

        for plan in plans:
            if not isinstance(plan, dict):
                continue
            plan_ref = plan.get("plan_ref") or plan.get("ref")
            if not plan_ref:
                print(f"[skip] plan without plan_ref: {plan}")
                continue
            print(f"[fetch] plan get {plan_ref}")
            detail = client.get_plan(
                str(plan_ref),
                start_date=start_d.isoformat(),
                end_date=end_d.isoformat(),
                include_movements=True,
            )
            out = plan_path(str(plan_ref))
            out.write_text(
                json.dumps(detail, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"  -> {out}")

    print("done")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync 训记 official plans to data/plans/")
    parser.add_argument("--start", help="YYYY-MM-DD (default today-7)")
    parser.add_argument("--end", help="YYYY-MM-DD (default today+30)")
    args = parser.parse_args()
    sync_plans(start=args.start, end=args.end)


if __name__ == "__main__":
    main()
