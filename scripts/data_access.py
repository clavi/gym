"""Unified read CLI; offline by default, network opt-in via --sync."""

from __future__ import annotations

import argparse
import json

if __package__:
    from .access import DataAccess, LocalDataUnavailable
    from .xunji_client import XunjiApiError
else:
    from access import DataAccess, LocalDataUnavailable
    from xunji_client import XunjiApiError


def main() -> None:
    parser = argparse.ArgumentParser(description="Local-first training/food/body/Agent-template access")
    sub = parser.add_subparsers(dest="domain", required=True)
    reindex = sub.add_parser("reindex", help="Offline import/rebuild of archived data and cursors")
    reindex.add_argument("--domain", dest="reindex_domain",
                         choices=("training", "food", "body", "food_templates", "templates"))
    for domain in ("training", "food", "body", "food-templates", "templates"):
        cmd = sub.add_parser(domain)
        cmd.add_argument("--sync", action="store_true", help="Explicitly allow remote reads")
        cmd.add_argument("--refresh", action="store_true", help="Ignore cached query (requires --sync)")
        cmd.add_argument("--json", action="store_true", help="Print the full returned data")
        if domain == "training":
            cmd.add_argument("--date", required=True, help="YYYY-MM-DD")
        elif domain in ("food", "body"):
            cmd.add_argument("--start", required=True, help="YYYY-MM-DD")
            cmd.add_argument("--end", required=True, help="YYYY-MM-DD")
            if domain == "body":
                cmd.add_argument("--types", nargs="+", help="Default all types; waist is spelled weist")
        elif domain == "templates":
            cmd.add_argument("--metadata-only", action="store_true", help="First sync only: exclude training content")
    args = parser.parse_args()
    if args.domain == "reindex":
        try:
            with DataAccess() as data:
                print(f"imported_archives={data.reindex_local(domain=args.reindex_domain)}")
        except (ValueError, OSError) as exc:
            raise SystemExit(str(exc)) from None
        return
    if args.refresh and not args.sync:
        parser.error("--refresh requires --sync")
    if args.domain == "templates" and args.refresh:
        parser.error("Templates use a persisted incremental cursor, not refresh")
    if args.domain == "templates" and args.metadata_only and not args.sync:
        parser.error("--metadata-only is a sync option")
    try:
        with DataAccess(allow_remote=args.sync) as data:
            if args.domain == "training":
                result = data.get_training(args.date, refresh=args.refresh)
            elif args.domain == "food":
                result = data.get_food(args.start, args.end, refresh=args.refresh)
            elif args.domain == "body":
                result = data.get_body(args.start, args.end, types=args.types, refresh=args.refresh)
            elif args.domain == "food-templates":
                result = data.get_food_templates(refresh=args.refresh)
            elif args.sync:
                result = data.sync_templates(include_content=False if args.metadata_only else None)
            else:
                result = data.get_templates()
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                res = result.get("res", result)
                for key in ("trains", "days", "records", "templates"):
                    if isinstance(res, dict) and key in res:
                        print(f"{key}={len(res[key])}")
                if isinstance(res, list):
                    print(f"records={len(res)}")
                if "cursor" in result:
                    print(f"cursor={result['cursor']}")
    except (XunjiApiError, LocalDataUnavailable, ValueError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
