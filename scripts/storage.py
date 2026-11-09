"""Append-only raw snapshots and a rebuildable SQLite query index."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

if __package__:
    from .responses import template_result
else:
    from responses import template_result


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def cache_key(domain: str, query: dict) -> str:
    return hashlib.sha256(json_text([domain, query]).encode("utf-8")).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    """For mutable indexes/state only; never use this to replace raw archives."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json_text(value) + "\n"
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def archive_json(root: Path, domain: str, query: dict, payload: dict) -> Path:
    if domain not in {"training", "food", "body", "food_templates", "templates"}:
        raise ValueError("Unknown archive domain")
    # Serialize before creating a file; invalid numbers cannot leave partial JSON.
    content = json_text(payload) + "\n"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = root / domain / "snapshots" / f"{cache_key(domain, query)[:16]}_{stamp}_{uuid.uuid4().hex}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    # Query metadata is separate from the unmodified API-shaped response. It
    # permits full offline reindexing, including empty training-day responses.
    manifest = path.with_suffix(".request.json")
    with manifest.open("x", encoding="utf-8") as stream:
        stream.write(json_text({"domain": domain, "query": query}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return path


def number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Invalid numeric record") from None
    if not math.isfinite(result):
        raise ValueError("Non-finite numeric record")
    return result


class Repository:
    def __init__(self, conn):
        self.conn = conn

    def cached(self, domain: str, query: dict) -> tuple[dict, Path] | None:
        row = self.conn.execute("SELECT raw_path FROM access_cache WHERE cache_key = ?",
                                (cache_key(domain, query),)).fetchone()
        if row is None:
            return None
        path = Path(row["raw_path"])
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8")), path

    def remember(self, domain: str, query: dict, payload: dict, path: Path) -> None:
        # Normalize and advance cache pointer in one transaction. Raw snapshots
        # remain untouched if normalization fails; the previous index stays valid.
        with self.conn:
            if domain == "training":
                if __package__:
                    from .normalize import normalize_payload
                else:
                    from normalize import normalize_payload
                res = payload.get("res", {})
                trains = res.get("trains", []) if isinstance(res, dict) else res
                if any(t.get("datestr", query["datestr"]) != query["datestr"] for t in trains):
                    raise ValueError("Training response contains an unrequested date")
                normalize_payload(self.conn, payload, datestr=query["datestr"], commit=False)
            elif domain == "food":
                for day in payload.get("res", {}).get("days", []):
                    d = day.get("datestr") or day.get("date", "")
                    if not query["start_date"] <= d <= query["end_date"]:
                        raise ValueError("Food response contains dates outside the requested range")
                # A successful detailed query is authoritative for its range,
                # including dates which now have no records.
                self.conn.execute("DELETE FROM food_record WHERE datestr BETWEEN ? AND ?",
                                  (query["start_date"], query["end_date"]))
                self.conn.execute("DELETE FROM food_day WHERE datestr BETWEEN ? AND ?",
                                  (query["start_date"], query["end_date"]))
                self._food(payload)
            elif domain == "body":
                for record in payload.get("res", {}).get("records", []):
                    if not query["start_date"] <= record.get("datestr", "") <= query["end_date"]:
                        raise ValueError("Body response contains dates outside the requested range")
                    if query.get("types") and record.get("type") not in query["types"]:
                        raise ValueError("Body response contains unrequested types")
                self._body(payload)
            self.conn.execute(
                "INSERT OR REPLACE INTO access_cache VALUES (?, ?, ?, ?, ?)",
                (cache_key(domain, query), domain, json_text(query), str(path.resolve()),
                 datetime.now(timezone.utc).isoformat()),
            )

    def covering_food(self, start: str, end: str) -> tuple[dict, Path] | None:
        for row in self.conn.execute(
            "SELECT query_json, raw_path FROM access_cache WHERE domain = 'food' ORDER BY updated_at DESC"
        ):
            query = json.loads(row[0])
            path = Path(row[1])
            if query["start_date"] <= start <= end <= query["end_date"] and path.is_file():
                return json.loads(path.read_text(encoding="utf-8")), path
        return None

    def _food(self, payload: dict) -> None:
        res = payload.get("res")
        if not isinstance(res, dict) or not isinstance(res.get("days"), list):
            raise ValueError("Food response must contain res.days")
        for day in res["days"]:
            d = day.get("datestr") or day.get("date")
            date.fromisoformat(d)
            foods = day.get("foods", {})
            records = foods.get("records", [])
            if not isinstance(records, list):
                raise ValueError("Food records must be a list")
            totals, limits = day.get("totals", {}), foods.get("limits", {})
            self.conn.execute("DELETE FROM food_record WHERE datestr = ?", (d,))
            self.conn.execute(
                "INSERT OR REPLACE INTO food_day VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (d, str(day["template_id"]) if "template_id" in day else None,
                 *[number(totals.get(k)) for k in ("totalCal", "totalProtein", "totalCarb", "totalFat", "totalWater")],
                 *[number(limits.get(k)) for k in ("protein", "carb", "fat")], json_text(day)),
            )
            for index, record in enumerate(records):
                self.conn.execute(
                    "INSERT INTO food_record VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (d, index, str(record["record_id"]) if "record_id" in record else None,
                     record.get("meal_type"), record["name"], number(record.get("amount")),
                     record.get("unit"), json_text(record)),
                )

    def _body(self, payload: dict) -> None:
        records = payload.get("res", {}).get("records")
        if not isinstance(records, list):
            raise ValueError("Body response must contain res.records")
        for record in records:
            d = record.get("datestr")
            date.fromisoformat(d)
            self.conn.execute(
                "INSERT OR REPLACE INTO body_record VALUES (?, ?, ?, ?, ?)",
                (d, record["type"], number(record.get("value")), record.get("unit"), json_text(record)),
            )

    def state(self, name: str) -> dict | None:
        row = self.conn.execute("SELECT value_json FROM access_state WHERE name = ?", (name,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_state(self, name: str, value: dict) -> None:
        self.conn.execute("INSERT OR REPLACE INTO access_state VALUES (?, ?)", (name, json_text(value)))

    def food_days(self, start: str, end: str) -> list[dict]:
        return [json.loads(row[0]) for row in self.conn.execute(
            "SELECT data_json FROM food_day WHERE datestr BETWEEN ? AND ? ORDER BY datestr", (start, end))]

    def body_records(self, start: str, end: str, types: list[str] | None = None) -> list[dict]:
        query = "SELECT data_json FROM body_record WHERE datestr BETWEEN ? AND ?"
        args: list = [start, end]
        if types:
            query += " AND type IN (" + ",".join("?" for _ in types) + ")"
            args.extend(types)
        return [json.loads(row[0]) for row in self.conn.execute(query + " ORDER BY datestr DESC, type", args)]

    def templates(self, *, include_deleted: bool = False) -> list[dict]:
        sql = "SELECT template_id, deleted, data_json FROM agent_template"
        if not include_deleted:
            sql += " WHERE deleted = 0"
        return [{"template_id": row[0], "deleted": bool(row[1]), "data": json.loads(row[2])}
                for row in self.conn.execute(sql + " ORDER BY template_id")]

    def apply_template_page(self, payload: dict, *, requested_cursor: int,
                            include_content: bool, path: Path) -> dict:
        res = template_result(payload)
        changes, next_cursor, has_more = res.get("changes"), res.get("next_cursor"), res.get("has_more")
        if "from_cursor" in res and res["from_cursor"] != requested_cursor:
            raise ValueError("Template response does not match the requested cursor")
        if not isinstance(changes, list) or type(next_cursor) is not int or next_cursor < requested_cursor:
            raise ValueError("Invalid template changes/cursor")
        if type(has_more) is not bool or (has_more and next_cursor <= requested_cursor):
            raise ValueError("Template pagination did not advance")
        with self.conn:
            # Acquire the SQLite writer lock *before* checking the cursor so two
            # sync processes cannot both commit a page based on an old cursor.
            self.conn.execute("UPDATE access_state SET value_json = value_json WHERE name = 'templates'")
            old = self.state("templates") or {"cursor": 0, "include_content": include_content}
            if old["cursor"] != requested_cursor:
                raise ValueError("Template cursor changed concurrently")
            if old.get("include_content") != include_content:
                raise ValueError("Template content mode must match the persisted sync mode")
            state = dict(old)
            for key in ("folder", "limits"):
                if key in res:
                    state[key] = res[key]
            last_revision = requested_cursor
            for change in changes:
                revision = change.get("revision")
                if type(revision) is not int or not last_revision < revision <= next_cursor:
                    raise ValueError("Template changes must be in increasing revision order")
                last_revision = revision
                entity = change.get("entity_type") or change.get("entity") or change.get("kind") or change.get("type")
                op = change.get("operation") or change.get("op") or change.get("action")
                if op is not None and op not in ("upsert", "create", "update", "delete", "deleted"):
                    raise ValueError("Unknown template change operation; raw response preserved")
                data = change.get("data")
                deleted = op in ("delete", "deleted") or change.get("deleted") is True
                if entity in ("folder", "folder_upsert", "folder_delete"):
                    deleted = deleted or entity == "folder_delete"
                    if not deleted and not isinstance(data, dict):
                        raise ValueError("Invalid folder change; raw response preserved")
                    state["folder"] = None if deleted else data
                    state["folder_deleted"] = deleted
                    if deleted:
                        self.conn.execute("UPDATE agent_template SET deleted = 1")
                    continue
                if entity not in (None, "template", "template_upsert", "template_delete"):
                    raise ValueError("Unknown template change entity; raw response preserved")
                deleted = deleted or entity == "template_delete"
                template_id = change.get("entity_id") or change.get("template_id")
                if template_id is None and isinstance(data, dict):
                    template_id = data.get("unique_id") or data.get("template_id") or data.get("id")
                if template_id is None or (not deleted and not isinstance(data, dict)):
                    raise ValueError("Invalid template change; raw response preserved")
                # A tombstone is retained even for a never-seen template. Full
                # content replaces old data; metadata-only updates may merge.
                existing = self.conn.execute("SELECT data_json FROM agent_template WHERE template_id = ?",
                                              (str(template_id),)).fetchone()
                merged = json.loads(existing[0]) if existing else {}
                if isinstance(data, dict):
                    if include_content and not deleted:
                        merged = data
                    else:
                        merged.update(data)
                self.conn.execute("INSERT OR REPLACE INTO agent_template VALUES (?, ?, ?)",
                                  (str(template_id), int(deleted), json_text(merged)))
            state.update(cursor=next_cursor, include_content=include_content, raw_path=str(path.resolve()))
            self.set_state("templates", state)
        return state
