"""Local-first data access. Network access must be explicitly enabled.

All read methods return the API-shaped payload. Instantiating this facade needs
no API keys; domain clients are created lazily only on a cache miss/refresh.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Callable

if __package__:
    from .db import connect, init_db
    from .paths import DATA_DIR
    from .responses import template_result
    from .storage import Repository, archive_json
    from .xunji_client import (XunjiBodyClient, XunjiClient, XunjiFoodClient, XunjiTemplateClient,
                               validate_body_types, validate_range)
else:
    from db import connect, init_db
    from paths import DATA_DIR
    from responses import template_result
    from storage import Repository, archive_json
    from xunji_client import (XunjiBodyClient, XunjiClient, XunjiFoodClient, XunjiTemplateClient,
                              validate_body_types, validate_range)


class LocalDataUnavailable(RuntimeError):
    pass


def validate_payload(domain: str, payload: dict) -> None:
    if not isinstance(payload, dict) or payload.get("success") is False:
        raise ValueError("Invalid or unsuccessful cached response")
    if domain == "templates":
        res = template_result(payload)
    elif "res" in payload:
        res = payload["res"]
    else:
        raise ValueError("Missing response result")
    if domain in ("food", "body", "food_templates") and payload.get("success") is not True:
        raise ValueError("Response must indicate success")
    if domain == "training":
        trains = res.get("trains") if isinstance(res, dict) else res
        if not isinstance(trains, list) or any(not isinstance(t, dict) for t in trains):
            raise ValueError("Training response must contain a trains list")
    else:
        field = {"food": "days", "body": "records", "food_templates": "templates", "templates": "changes"}[domain]
        if not isinstance(res, dict) or not isinstance(res.get(field), list):
            raise ValueError(f"Response must contain res.{field}")
        if any(not isinstance(item, dict) for item in res[field]):
            raise ValueError(f"Invalid record in res.{field}")
        if domain == "food":
            for day in res["days"]:
                datestr = day.get("datestr") or day.get("date")
                if not isinstance(datestr, str):
                    raise ValueError("Food day is missing its date")
                date.fromisoformat(datestr)
                foods = day.get("foods", {})
                if not isinstance(foods, dict) or not isinstance(day.get("totals", {}), dict):
                    raise ValueError("Invalid food day data")
                if not isinstance(foods.get("limits", {}), dict) or not isinstance(foods.get("records", []), list):
                    raise ValueError("Invalid food limits/records")
                if any(not isinstance(r, dict) or not isinstance(r.get("name"), str) for r in foods.get("records", [])):
                    raise ValueError("Invalid food record")
        elif domain == "body":
            for record in res["records"]:
                if not isinstance(record.get("datestr"), str) or not isinstance(record.get("type"), str):
                    raise ValueError("Body record must include date and type")
                date.fromisoformat(record["datestr"])


class DataAccess:
    def __init__(self, *, allow_remote: bool = False, data_dir: Path | None = None,
                 clients: dict[str, Callable] | None = None):
        self.data_dir = Path(data_dir) if data_dir is not None else DATA_DIR
        self.raw_dir = self.data_dir / "raw"
        conn = connect(self.data_dir / "training.db")
        try:
            self.conn = init_db(conn)
        except Exception:
            conn.close()
            raise
        self.repository = Repository(self.conn)
        self.allow_remote = allow_remote
        self._factories = clients or {
            "training": XunjiClient, "food": XunjiFoodClient,
            "body": XunjiBodyClient, "templates": XunjiTemplateClient,
        }
        self._clients: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self.conn.close()

    def _client(self, domain: str):
        if not self.allow_remote:
            raise LocalDataUnavailable("No cached data; explicitly enable allow_remote to fetch")
        if domain not in self._clients:
            self._clients[domain] = self._factories[domain]()
        return self._clients[domain]

    def _read(self, domain: str, query: dict, fetch: Callable, *, refresh: bool = False,
              legacy: Path | None = None) -> dict:
        if refresh and not self.allow_remote:
            raise LocalDataUnavailable("Refresh requires explicit remote access")
        if not refresh:
            cached = self.repository.cached(domain, query)
            if cached:
                validate_payload(domain, cached[0])
                if domain == "food":
                    return self._food_view(cached[0], query["start_date"], query["end_date"])
                return cached[0]
            if legacy is not None and legacy.is_file():
                payload = json.loads(legacy.read_text(encoding="utf-8"))
                validate_payload(domain, payload)
                self.repository.remember(domain, query, payload, legacy)
                if domain == "food":
                    return self._food_view(payload, query["start_date"], query["end_date"])
                return payload
        if not self.allow_remote:
            raise LocalDataUnavailable(f"No local data for {domain}; use explicit sync/remote access")
        payload = fetch()
        validate_payload(domain, payload)
        path = archive_json(self.raw_dir, domain, query, payload)
        self.repository.remember(domain, query, payload, path)
        return payload

    def get_training(self, datestr: str, *, refresh: bool = False) -> dict:
        date.fromisoformat(datestr)
        query = {"datestr": datestr, "include_full_data": True}
        return self._read(
            "training", query, lambda: self._client("training").get_training(datestr, include_full_data=True),
            refresh=refresh, legacy=self.raw_dir / "training" / datestr[:4] / f"{datestr}.json",
        )

    def get_food(self, start: str, end: str, *, refresh: bool = False) -> dict:
        validate_range(start, end)
        if refresh and not self.allow_remote:
            raise LocalDataUnavailable("Refresh requires explicit remote access")
        query = {"start_date": start, "end_date": end, "include_detail": True}
        legacy = self.raw_dir / "food" / f"{start}_{end}.json"
        if refresh:
            # Import a covering old archive before refreshing a subset. Otherwise
            # importing it later could overwrite the newly refreshed day's data.
            for candidate in sorted((self.raw_dir / "food").glob("????-??-??_????-??-??.json"), reverse=True):
                a, b = candidate.stem.split("_")
                if a <= start <= end <= b:
                    self.get_food(a, b)
                    break
        # Old range archives can satisfy a subset without any API request. Import
        # the full archive under its own range, then return a filtered view.
        if not refresh and not self.repository.cached("food", query) and not legacy.is_file():
            covering = self.repository.covering_food(start, end)
            if covering:
                full = covering[0]
                validate_payload("food", full)
                return self._food_view(full, start, end)
            for candidate in sorted((self.raw_dir / "food").glob("????-??-??_????-??-??.json"), reverse=True):
                a, b = candidate.stem.split("_")
                if a <= start <= end <= b:
                    full = self.get_food(a, b)
                    return self._food_view(full, start, end)
        return self._read("food", query, lambda: self._client("food").get_food(start, end),
                          refresh=refresh, legacy=legacy)

    def _food_view(self, payload: dict, start: str, end: str) -> dict:
        # Coverage comes from a completed cached query; day content comes from
        # the normalized store so overlapping refreshes cannot return stale days.
        return {**payload, "res": {**payload["res"], "start_date": start, "end_date": end,
                                   "days": self.repository.food_days(start, end)}}

    def get_food_templates(self, *, refresh: bool = False) -> dict:
        return self._read("food_templates", {}, lambda: self._client("food").list_templates(),
                          refresh=refresh, legacy=self.raw_dir / "food" / "templates" / "list.json")

    def get_body(self, start: str, end: str, *, types: list[str] | None = None,
                 refresh: bool = False, page_size: int = 500, max_pages: int = 1000) -> dict:
        validate_range(start, end)
        types = validate_body_types(types)
        if (type(page_size) is not int or type(max_pages) is not int
                or not 1 <= page_size <= 500 or max_pages < 1):
            raise ValueError("Invalid body page size or page count")
        records, seen = [], set()
        offset, latest = 0, None
        for _ in range(max_pages):
            query = {"start_date": start, "end_date": end, "types": types,
                     "include_latest": offset == 0, "include_records": True,
                     "limit": page_size, "offset": offset}
            payload = self._read(
                "body", query, lambda: self._client("body").get_body(
                    start, end, types=types, include_latest=offset == 0, limit=page_size, offset=offset),
                refresh=refresh,
            )
            res, page = payload["res"], payload["res"]["records"]
            if len(page) > page_size:
                raise ValueError("Body server exceeded requested page size")
            if offset == 0:
                latest = res.get("latest")
            for record in page:
                key = (record.get("datestr"), record.get("type"))
                if key in seen:
                    raise ValueError("Body pagination repeated records; query may have changed, retry with refresh")
                seen.add(key)
                records.append(record)
            offset += len(page)
            has_more = res.get("has_more")
            if has_more is not None and type(has_more) is not bool:
                raise ValueError("Invalid body has_more")
            if has_more and not page:
                raise ValueError("Body pagination did not advance")
            if has_more is False or (has_more is None and len(page) < page_size):
                by_type: dict[str, list] = {}
                for record in records:
                    by_type.setdefault(record["type"], []).append(record)
                # This is a combined local view; each original page is archived
                # separately and never replaced by this synthesized response.
                return {"success": True, "res": {"start_date": start, "end_date": end,
                        "records": records, "latest": latest, "by_type": by_type}}
        raise ValueError("Body pagination exceeded safety limit; no completion was claimed")

    def get_templates(self, *, include_deleted: bool = False) -> dict:
        state = self.repository.state("templates")
        if state is None:
            raise LocalDataUnavailable("Agent templates have not been synced locally")
        return {**state, "templates": self.repository.templates(include_deleted=include_deleted)}

    def sync_templates(self, *, include_content: bool | None = None, max_pages: int = 1000) -> dict:
        if not self.allow_remote:
            raise LocalDataUnavailable("Template sync requires explicit remote access")
        if type(max_pages) is not int or max_pages < 1:
            raise ValueError("max_pages must be positive")
        if include_content is not None and type(include_content) is not bool:
            raise ValueError("include_content must be boolean")
        state = self.repository.state("templates")
        if include_content is None:
            include_content = state.get("include_content", True) if state else True
        if state and state.get("include_content") != include_content:
            raise ValueError("Cannot change persisted template content mode or silently reset cursor")
        cursor = state["cursor"] if state else 0
        for _ in range(max_pages):
            # Each committed page persists cursor+folder+limits+tombstones in one
            # transaction. Interruption resumes from the last committed page.
            payload = self._client("templates").sync_templates(cursor, include_content=include_content)
            validate_payload("templates", payload)
            query = {"cursor": cursor, "limit": 15, "include_content": include_content}
            path = archive_json(self.raw_dir, "templates", query, payload)
            state = self.repository.apply_template_page(
                payload, requested_cursor=cursor, include_content=include_content, path=path)
            cursor = state["cursor"]
            if not template_result(payload)["has_more"]:
                return self.get_templates()
        raise ValueError("Template pagination exceeded safety limit; saved cursor can resume")

    def reindex_local(self, *, domain: str | None = None) -> int:
        """Replay old archives then immutable snapshots; never use a client."""
        if domain not in (None, "training", "food", "body", "food_templates", "templates"):
            raise ValueError("Unknown reindex domain")
        count = 0

        def remember(kind: str, query: dict, payload: dict, path: Path) -> None:
            nonlocal count
            if domain is not None and domain != kind:
                return
            validate_payload(kind, payload)
            if kind == "templates":
                current = self.repository.state("templates")
                if current and template_result(payload)["next_cursor"] <= current["cursor"]:
                    return
                self.repository.apply_template_page(
                    payload, requested_cursor=query["cursor"],
                    include_content=query["include_content"], path=path)
            else:
                self.repository.remember(kind, query, payload, path)
            count += 1

        for path in sorted((self.raw_dir / "training").glob("????/????-??-??.json")):
            if domain not in (None, "training"):
                break
            remember("training", {"datestr": path.stem, "include_full_data": True},
                     json.loads(path.read_text(encoding="utf-8")), path)
        for path in sorted((self.raw_dir / "food").glob("????-??-??_????-??-??.json")):
            if domain not in (None, "food"):
                break
            start, end = path.stem.split("_")
            remember("food", {"start_date": start, "end_date": end, "include_detail": True},
                     json.loads(path.read_text(encoding="utf-8")), path)
        listed = self.raw_dir / "food" / "templates" / "list.json"
        if domain in (None, "food_templates") and listed.is_file():
            remember("food_templates", {}, json.loads(listed.read_text(encoding="utf-8")), listed)
        manifests = sorted(self.raw_dir.glob("*/snapshots/*.request.json"),
                           key=lambda p: p.name.split("_")[1])
        for manifest in manifests:
            request = json.loads(manifest.read_text(encoding="utf-8"))
            if domain is not None and request["domain"] != domain:
                continue
            path = manifest.with_name(manifest.name.replace(".request.json", ".json"))
            remember(request["domain"], request["query"],
                     json.loads(path.read_text(encoding="utf-8")), path)
        return count
