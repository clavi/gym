"""Read-only coverage inspection and precise training-source validation."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

if __package__:
    from .analysis_semantics import trains
    from .db import init_db
    from .normalize import normalize_payload
else:
    from analysis_semantics import trains
    from db import init_db
    from normalize import normalize_payload


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dates(start, end):
    current = date.fromisoformat(start)
    stop = date.fromisoformat(end)
    while current <= stop:
        yield current.isoformat()
        current += timedelta(days=1)


def rows_for_day(conn, day):
    """Canonical normalization projection, deliberately excluding volatile IDs."""
    sessions = [dict(r) for r in conn.execute(
        "SELECT * FROM training_session WHERE datestr=? ORDER BY id", (day,))]
    result = []
    for session in sessions:
        sid = session.pop("id")
        children = {}
        for table in ("training_set", "cardio_record", "heart_rate_summary"):
            rows = []
            for row in conn.execute(f"SELECT * FROM {table} WHERE session_id=? ORDER BY id", (sid,)):
                item = dict(row)
                item.pop("id")
                item.pop("session_id")
                rows.append(item)
            children[table] = rows
        result.append({"session": session, **children})
    return result


def normalized_matches(conn, payload, day):
    temp = sqlite3.connect(":memory:")
    temp.row_factory = sqlite3.Row
    try:
        init_db(temp)
        normalize_payload(temp, payload, day)
        return rows_for_day(temp, day) == rows_for_day(conn, day)
    except (ValueError, TypeError, OverflowError, sqlite3.Error):
        return False
    finally:
        temp.close()


def training_sources(conn, raw_root, start, end):
    """Find latest valid evidence, but claim coverage only if it matches SQLite.

    Old success does not certify freshness. Index timestamps never become API
    capture times. Snapshot timestamp is available only for archive_json names.
    """
    root = Path(raw_root)
    candidates = {d: [] for d in dates(start, end)}
    cached = {}
    try:
        rows = conn.execute("SELECT query_json, raw_path FROM access_cache WHERE domain='training'")
        for row in rows:
            query = json.loads(row[0])
            day = query.get("datestr")
            if day in candidates:
                cached[day] = Path(row[1]).resolve()
    except sqlite3.OperationalError:
        pass
    for meta in (root / "training" / "snapshots").glob("*.request.json"):
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            day = info.get("query", {}).get("datestr")
            if info.get("domain") == "training" and day in candidates:
                path = meta.with_name(meta.name.replace(".request.json", ".json"))
                # The archive name is hash_UTC_uuid. Not a filesystem mtime.
                stamp = path.stem.split("_")[1] if "_" in path.stem else ""
                candidates[day].append((stamp, path, meta))
        except (ValueError, IndexError, OSError):
            continue
    result = []
    for day, options in candidates.items():
        legacy = root / "training" / day[:4] / f"{day}.json"
        options.append(("", legacy, None))
        if day in cached and all(p.resolve() != cached[day] for _, p, _ in options):
            options.append(("", cached[day], None))
        options.sort(key=lambda v: (v[0], v[1].name), reverse=True)
        errors = []
        selected = None
        for stamp, path, meta in options:
            if not path.is_file():
                continue
            try:
                content = path.read_bytes()
                payload = json.loads(content.decode("utf-8"))
                records = trains(payload)
                if any(t.get("datestr", day) != day for t in records):
                    raise ValueError("source_date_mismatch")
                matches = normalized_matches(conn, payload, day)
                query = json.loads(meta.read_text(encoding="utf-8"))["query"] if meta else {"datestr": day, "include_full_data": True}
                selected = {"date": day, "status": "synced_records" if records else "synced_empty",
                            "domain": "training", "raw_path": str(path.resolve()),
                            "sha256": hashlib.sha256(content).hexdigest(),
                            "request_path": str(meta.resolve()) if meta else None,
                            "request_sha256": sha256(meta) if meta else None,
                            "query": {k: query.get(k) for k in ("datestr", "include_full_data")},
                            "query_basis": "request_metadata" if meta else "legacy_archive_convention",
                            "capture_time": "unknown", "archive_time": stamp or "unknown", "payload": payload,
                            "projection_matches": matches, "source_granularity": "day_snapshot",
                            "errors": errors}
                if not matches:
                    selected["status"] = "raw_not_indexed_or_mismatch"
                elif query.get("include_full_data") is not True:
                    selected["status"] = "response_not_full"
                elif payload.get("truncated") is True or isinstance(payload.get("res"), dict) and payload["res"].get("truncated") is True:
                    selected["status"] = "response_truncated"
                break
            except (ValueError, OSError, TypeError):
                errors.append({"path": str(path), "reason": "invalid_source"})
        result.append(selected or {"date": day, "status": "coverage_unknown", "capture_time": "unknown",
                                   "payload": None, "projection_matches": False, "errors": errors})
    return result


def food_sources(conn, raw_root, start, end):
    """Validate daily totals/limits/records against the latest range snapshot.

    Returns frozen evidence, including Raw-but-not-indexed dates. Zero missing
    days in a successful response means no *record*, not zero intake.
    """
    root = Path(raw_root) / "food"
    options = []
    for path in root.glob("????-??-??_????-??-??.json"):
        qs, qe = path.stem.split("_")
        options.append(("", path, None, {"start_date": qs, "end_date": qe, "include_detail": True}))
    for meta in root.glob("snapshots/*.request.json"):
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            q = info["query"]
            if info["domain"] == "food" and q.get("include_detail") is True:
                path = meta.with_name(meta.name.replace(".request.json", ".json"))
                options.append((path.stem.split("_")[1], path, meta, q))
        except (ValueError, KeyError, IndexError, OSError):
            continue
    # Cached archive paths may come from a caller-selected archive location.
    for row in conn.execute("SELECT query_json,raw_path FROM access_cache WHERE domain='food'"):
        q, path = json.loads(row[0]), Path(row[1])
        if q.get("include_detail") is True and all(path.resolve() != p.resolve() for _, p, _, _ in options):
            options.append(("", path, None, q))
    materialized = {r[0]: json.loads(r[1]) for r in conn.execute(
        "SELECT datestr,data_json FROM food_day WHERE datestr BETWEEN ? AND ?", (start, end))}
    result = {}
    for stamp, path, meta, q in sorted(options, key=lambda v: (v[0], v[1].name), reverse=True):
        qs, qe = q.get("start_date", ""), q.get("end_date", "")
        selected = [d for d in dates(start, end) if qs <= d <= qe and d not in result]
        if not selected or not path.is_file():
            continue
        try:
            content = path.read_bytes()
            payload = json.loads(content.decode("utf-8"))
            days = payload.get("res", {}).get("days")
            if payload.get("success") is not True or not isinstance(days, list):
                continue
            mapped = {d.get("datestr", d.get("date")): d for d in days if isinstance(d, dict)}
            if len(mapped) != len(days) or any(not qs <= d <= qe for d in mapped):
                continue
            for d in selected:
                record = mapped.get(d)
                matches = materialized.get(d) == record
                result[d] = {"domain": "food", "date": d, "status":
                             ("synced_records" if record else "synced_empty") if matches else "raw_not_indexed_or_mismatch",
                             "projection_matches": matches, "capture_time": "unknown", "archive_time": stamp or "unknown",
                             "raw_path": str(path.resolve()), "sha256": hashlib.sha256(content).hexdigest(),
                             "request_path": str(meta.resolve()) if meta else None,
                             "request_sha256": sha256(meta) if meta else None,
                             "query": {k: q.get(k) for k in ("start_date", "end_date", "include_detail")},
                             "payload": record, "source_granularity": "range_snapshot_daily_projection"}
        except (ValueError, TypeError, AttributeError, OSError):
            continue
    return [result.get(d, {"domain": "food", "date": d, "status": "coverage_unknown",
                            "capture_time": "unknown", "projection_matches": False, "payload": None}) for d in dates(start, end)]


def body_sources(conn, start, end):
    """Check indexed page chains and their materialized records, scoped by types.

    Server pagination is not an atomic remote snapshot. The evidence states only
    what these successfully indexed pages contained at their collection times.
    """
    chains = {}
    for row in conn.execute("SELECT query_json,raw_path FROM access_cache WHERE domain='body'"):
        try:
            q = json.loads(row[0])
            if q.get("include_records") is not True or q["start_date"] > end or q["end_date"] < start:
                continue
            key = (q["start_date"], q["end_date"], tuple(q.get("types") or []), q["limit"])
            path = Path(row[1])
            content = path.read_bytes()
            payload = json.loads(content.decode("utf-8"))
            res = payload.get("res", {})
            if payload.get("success") is not True or not isinstance(res.get("records"), list):
                continue
            meta = path.with_suffix(".request.json")
            stamp = path.stem.split("_")[1] if path.parent.name == "snapshots" and "_" in path.stem else "unknown"
            safe_query = {k: q.get(k) for k in ("start_date", "end_date", "types", "include_latest", "include_records", "limit", "offset")}
            chains.setdefault(key, {})[q["offset"]] = {"query": safe_query, "payload": res, "raw_path": str(path.resolve()),
                "sha256": hashlib.sha256(content).hexdigest(), "capture_time": "unknown", "archive_time": stamp,
                "request_path": str(meta.resolve()) if meta.is_file() else None,
                "request_sha256": sha256(meta) if meta.is_file() else None}
        except (ValueError, KeyError, IndexError, OSError, TypeError):
            continue
    output = {}
    for (qs, qe, kinds, limit), pages in sorted(chains.items(), key=lambda item: max(p["archive_time"] for p in item[1].values()), reverse=True):
        offset, records, used, complete = 0, [], [], False
        while offset in pages:
            page = pages[offset]
            batch = page["payload"]["records"]
            more = page["payload"].get("has_more")
            if any(not isinstance(r, dict) or not qs <= r.get("datestr", "") <= qe or
                   (kinds and r.get("type") not in kinds) for r in batch):
                break
            records.extend(batch)
            used.append(page)
            if more is False or (more is None and len(batch) < limit):
                complete = True
                break
            if more is not True and more is not None or not batch:
                break
            offset += len(batch)
        keys = [(r.get("datestr"), r.get("type")) for r in records]
        if len(keys) != len(set(keys)):
            complete = False
        stored = [json.loads(r[0]) for r in conn.execute("SELECT data_json FROM body_record WHERE datestr BETWEEN ? AND ?", (qs, qe))
                  if not kinds or json.loads(r[0]).get("type") in kinds]
        canonical = lambda rows: sorted((json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows))
        matches = complete and canonical(records) == canonical(stored)
        for d in dates(start, end):
            if not qs <= d <= qe or d in output:
                continue
            output[d] = {"domain": "body", "date": d, "status": ("synced_scoped_types" if kinds else "synced_records" if any(r["datestr"] == d for r in records) else "synced_empty") if matches else "incomplete_pages_or_projection_mismatch",
                         "projection_matches": matches, "types": list(kinds) or "all", "pages_complete": complete,
                         "capture_time": "unknown", "archive_time": max((p["archive_time"] for p in used), default="unknown"),
                         "payload": [r for r in records if r["datestr"] == d], "pages": used,
                         "source_granularity": "indexed_page_chain"}
    return [output.get(d, {"domain": "body", "date": d, "status": "coverage_unknown",
                          "capture_time": "unknown", "projection_matches": False, "payload": None}) for d in dates(start, end)]
