"""Offline persistence, compatibility and incremental-sync regression tests."""

import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from access import DataAccess, LocalDataUnavailable
from normalize import normalize_payload
from storage import archive_json, atomic_json, json_text


def food_day(d="2026-11-04", *, empty=False):
    return {"datestr": d, "date": d, "template_id": -1,
            "totals": {"totalCal": 100, "totalProtein": 8, "totalCarb": 6, "totalFat": 5, "totalWater": 0},
            "foods": {"limits": {"protein": "134", "carb": 179, "fat": 60},
                      "water": {"values": [], "limit": 0},
                      "records": [] if empty else [
                          {"record_id": "0", "meal_type": "morning", "name": "牛奶", "amount": 100,
                           "unit": "g", "ntr": {"cal": 100, "protein": 8, "carb": 6, "fat": 5}}]}}


def food(days):
    return {"success": True, "res": {"days": days}}


def training(title="训练", *, invalid=False):
    return {"res": {"trains": [{"datestr": "2026-11-04", "localid": "bad" if invalid else 1,
                                 "title": title, "movements": []}]}}


def template_page(cursor, changes=None, has_more=False, **extra):
    return {"success": True, "res": {"next_cursor": cursor, "has_more": has_more,
                                      "changes": changes or [], **extra}}


def change(revision, template_id="a", *, deleted=False, **data):
    return {"revision": revision, "entity": "template", "op": "delete" if deleted else "upsert",
            "template_id": template_id, "data": None if deleted else {"template_id": template_id, **data}}


class StubClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.closed = False

    def take(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    get_training = get_food = get_body = sync_templates = list_templates = take

    def close(self):
        self.closed = True


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gym-access-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def access(self, *, remote=False, **stubs):
        data = DataAccess(data_dir=self.root, allow_remote=remote,
                          clients={name: (lambda stub=stub: stub) for name, stub in stubs.items()})
        self.addCleanup(data.close)
        return data

    def legacy(self, relative, value):
        path = self.root / "raw" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json_text(value), encoding="utf-8")
        return path

    def test_offline_cache_miss_never_constructs_client(self):
        data = self.access()
        with self.assertRaises(LocalDataUnavailable):
            data.get_training("2026-11-04")
        self.assertEqual(data._clients, {})
        with self.assertRaises(LocalDataUnavailable):
            data.get_training("2026-11-04", refresh=True)

    def test_legacy_training_is_imported_without_api_key(self):
        path = self.legacy("training/2026/2026-11-04.json", training())
        before = path.read_bytes()
        data = self.access()
        self.assertEqual(data.get_training("2026-11-04"), training())
        self.assertEqual(data.conn.execute("SELECT count(*) FROM training_session").fetchone()[0], 1)
        self.assertEqual(path.read_bytes(), before)

    def test_refresh_never_overwrites_original_and_cache_is_reused(self):
        path = self.legacy("training/2026/2026-11-04.json", training("旧"))
        before = path.read_bytes()
        client = StubClient([training("新")])
        data = self.access(remote=True, training=client)
        data.get_training("2026-11-04")
        data.get_training("2026-11-04", refresh=True)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(data.get_training("2026-11-04")["res"]["trains"][0]["title"], "新")
        self.assertEqual(data.conn.execute("SELECT title FROM training_session").fetchone()[0], "新")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(list((self.root / "raw/training/snapshots").glob("*.request.json"))), 1)

    def test_invalid_remote_response_does_not_clear_training_or_cache(self):
        client = StubClient([training(), {"success": False, "res": {"trains": []}}])
        data = self.access(remote=True, training=client)
        data.get_training("2026-11-04")
        with self.assertRaises(ValueError):
            data.get_training("2026-11-04", refresh=True)
        self.assertEqual(data.conn.execute("SELECT count(*) FROM training_session").fetchone()[0], 1)
        self.assertEqual(data.get_training("2026-11-04"), training())

    def test_normalization_failure_rolls_back_training_and_cache(self):
        client = StubClient([training(), training(invalid=True)])
        data = self.access(remote=True, training=client)
        data.get_training("2026-11-04")
        with self.assertRaises(ValueError):
            data.get_training("2026-11-04", refresh=True)
        self.assertEqual(data.get_training("2026-11-04"), training())
        self.assertEqual(data.conn.execute("SELECT count(*) FROM training_session").fetchone()[0], 1)

    def test_direct_normalization_rejects_errors_before_deleting(self):
        data = self.access()
        normalize_payload(data.conn, training(), "2026-11-04")
        for payload in ({}, {"res": {}}, {"res": {"trains": None}}, {"res": {"trains": [1]}}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                normalize_payload(data.conn, payload, "2026-11-04")
        self.assertEqual(data.conn.execute("SELECT count(*) FROM training_session").fetchone()[0], 1)

    def test_food_legacy_subset_preserves_limits_water_and_full_payload(self):
        full = food([food_day(), food_day("2026-11-05")])
        path = self.legacy("food/2025-11-05_2026-11-05.json", full)
        before = path.read_bytes()
        data = self.access()
        result = data.get_food("2026-11-04", "2026-11-04")
        self.assertEqual(len(result["res"]["days"]), 1)
        row = data.conn.execute("SELECT protein_limit, water FROM food_day WHERE datestr = '2026-11-04'").fetchone()
        self.assertEqual(tuple(row), (134, 0))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(data._clients, {})

    def test_food_record_ids_are_not_globally_unique(self):
        records = food([food_day(), food_day("2026-11-05")])
        data = self.access(remote=True, food=StubClient([records]))
        data.get_food("2026-11-04", "2026-11-05")
        self.assertEqual(data.conn.execute("SELECT count(*) FROM food_record").fetchone()[0], 2)

    def test_new_food_snapshot_serves_subset_offline(self):
        client = StubClient([food([food_day(), food_day("2026-11-05")])])
        data = self.access(remote=True, food=client)
        data.get_food("2026-11-04", "2026-11-05")
        data.allow_remote = False
        self.assertEqual(len(data.get_food("2026-11-05", "2026-11-05")["res"]["days"]), 1)
        self.assertEqual(len(client.calls), 1)

    def test_empty_food_refresh_clears_only_queried_range(self):
        client = StubClient([food([food_day(), food_day("2026-11-05")]), food([])])
        data = self.access(remote=True, food=client)
        data.get_food("2026-11-04", "2026-11-05")
        data.get_food("2026-11-04", "2026-11-04", refresh=True)
        self.assertEqual(len(data.repository.food_days("2026-11-04", "2026-11-05")), 1)
        self.assertEqual(data.conn.execute("SELECT datestr FROM food_record").fetchone()[0], "2026-11-05")
        self.assertEqual(len(data.get_food("2026-11-04", "2026-11-05")["res"]["days"]), 1)

    def test_food_refresh_subset_before_first_legacy_read_keeps_new_limits(self):
        self.legacy("food/2025-11-05_2026-11-05.json", food([food_day()]))
        updated = food_day()
        updated["foods"]["limits"]["protein"] = 150
        data = self.access(remote=True, food=StubClient([food([updated])]))
        data.get_food("2026-11-04", "2026-11-04", refresh=True)
        full = data.get_food("2025-11-05", "2026-11-05")
        self.assertEqual(full["res"]["days"][0]["foods"]["limits"]["protein"], 150)

    def test_food_bad_numeric_value_rolls_back_range(self):
        bad = food_day()
        bad["foods"]["limits"]["fat"] = "NaN"
        data = self.access(remote=True, food=StubClient([food([food_day()]), food([bad])]))
        data.get_food("2026-11-04", "2026-11-04")
        with self.assertRaises(ValueError):
            data.get_food("2026-11-04", "2026-11-04", refresh=True)
        self.assertEqual(data.conn.execute("SELECT fat_limit FROM food_day").fetchone()[0], 60)

    def test_food_templates_distinct_from_personal_templates(self):
        self.legacy("food/templates/list.json", {"success": True, "res": {"templates": []}})
        data = self.access()
        self.assertEqual(data.get_food_templates()["res"]["templates"], [])
        with self.assertRaises(LocalDataUnavailable):
            data.get_templates()

    def test_body_pages_are_aggregated_and_each_cached(self):
        a = {"datestr": "2026-11-04", "type": "weight", "value": 70, "unit": "kg"}
        b = {"datestr": "2026-11-03", "type": "weight", "value": 71, "unit": "kg"}
        client = StubClient([
            {"success": True, "res": {"records": [a], "latest": {"weight": a}}},
            {"success": True, "res": {"records": [b]}},
            {"success": True, "res": {"records": []}},
        ])
        data = self.access(remote=True, body=client)
        result = data.get_body("2026-11-01", "2026-11-05", types=["weight"], page_size=1)
        self.assertEqual(result["res"]["records"], [a, b])
        self.assertEqual(result["res"]["latest"], {"weight": a})
        self.assertEqual([c[1]["offset"] for c in client.calls], [0, 1, 2])
        data.allow_remote = False
        self.assertEqual(data.get_body("2026-11-01", "2026-11-05", types=["weight"], page_size=1), result)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(len(data.repository.body_records("2026-11-01", "2026-11-05")), 2)

    def test_body_repeated_page_does_not_loop_forever(self):
        record = {"datestr": "2026-11-04", "type": "weight", "value": 70, "unit": "kg"}
        page = {"success": True, "res": {"records": [record]}}
        client = StubClient([page, page])
        data = self.access(remote=True, body=client)
        with self.assertRaisesRegex(ValueError, "repeated"):
            data.get_body("2026-11-01", "2026-11-05", page_size=1)
        self.assertEqual(len(client.calls), 2)

    def test_body_empty_has_more_is_rejected(self):
        data = self.access(remote=True, body=StubClient([{"success": True, "res": {"records": [], "has_more": True}}]))
        with self.assertRaisesRegex(ValueError, "did not advance"):
            data.get_body("2026-11-01", "2026-11-05")

    def test_templates_incremental_pages_metadata_and_tombstones(self):
        client = StubClient([
            template_page(1, [change(1, name="推 A", movements=[])], True,
                          folder={"id": "agent", "name": "Agent 模版"}, limits={"templates": 14}),
            template_page(2, [change(2, deleted=True)]),
            template_page(3),
        ])
        data = self.access(remote=True, templates=client)
        result = data.sync_templates()
        self.assertEqual(result["templates"], [])
        self.assertEqual(result["folder"]["id"], "agent")
        self.assertEqual(result["limits"], {"templates": 14})
        self.assertTrue(data.get_templates(include_deleted=True)["templates"][0]["deleted"])
        result = data.sync_templates()
        self.assertEqual(result["cursor"], 3)
        self.assertEqual([c[0][0] for c in client.calls], [0, 1, 2])

    def test_template_interruption_resumes_committed_cursor(self):
        client = StubClient([template_page(1, [change(1, name="A")], True), RuntimeError("interrupted"), template_page(2)])
        data = self.access(remote=True, templates=client)
        with self.assertRaises(RuntimeError):
            data.sync_templates()
        self.assertEqual(data.get_templates()["cursor"], 1)
        self.assertEqual(data.sync_templates()["cursor"], 2)
        self.assertEqual([c[0][0] for c in client.calls], [0, 1, 1])

    def test_template_nonadvancing_page_does_not_checkpoint(self):
        data = self.access(remote=True, templates=StubClient([template_page(0, has_more=True)]))
        with self.assertRaisesRegex(ValueError, "did not advance"):
            data.sync_templates()
        self.assertIsNone(data.repository.state("templates"))

    def test_bad_template_page_rolls_back_all_changes_and_cursor(self):
        bad = change(3, name="bad")
        bad["revision"] = 1
        data = self.access(remote=True, templates=StubClient([
            template_page(1, [change(1, name="old")]),
            template_page(3, [change(2, name="new"), bad]),
        ]))
        data.sync_templates()
        with self.assertRaises(ValueError):
            data.sync_templates()
        result = data.get_templates()
        self.assertEqual(result["cursor"], 1)
        self.assertEqual(result["templates"][0]["data"]["name"], "old")

    def test_template_folder_delete_tombstones_all_without_recreation(self):
        client = StubClient([
            template_page(1, [change(1, name="A")]),
            template_page(2, [{"revision": 2, "entity": "folder", "op": "delete", "data": None}]),
        ])
        data = self.access(remote=True, templates=client)
        data.sync_templates()
        result = data.sync_templates()
        self.assertTrue(result["folder_deleted"])
        self.assertEqual(result["templates"], [])
        self.assertEqual(len(client.calls), 2)

    def test_template_mode_change_never_resets_cursor(self):
        client = StubClient([template_page(1)])
        data = self.access(remote=True, templates=client)
        data.sync_templates(include_content=False)
        with self.assertRaises(ValueError):
            data.sync_templates(include_content=True)
        self.assertEqual(len(client.calls), 1)

    def test_full_template_update_replaces_removed_fields(self):
        data = self.access(remote=True, templates=StubClient([
            template_page(1, [change(1, name="A", rules={"old": True})]),
            template_page(2, [change(2, name="B")]),
        ]))
        data.sync_templates()
        result = data.sync_templates()
        self.assertNotIn("rules", result["templates"][0]["data"])

    def test_reindex_domain_does_not_parse_other_domain_payloads(self):
        self.legacy("training/2026/2026-11-04.json", training())
        self.legacy("food/2026-11-04_2026-11-05.json", {"invalid": "not a food response"})
        data = self.access()
        self.assertEqual(data.reindex_local(domain="training"), 1)

    def test_templates_require_explicit_remote_permission(self):
        data = self.access()
        with self.assertRaises(LocalDataUnavailable):
            data.sync_templates()

    def test_snapshots_are_distinct_and_have_recovery_metadata(self):
        query = {"datestr": "2026-11-04", "include_full_data": True}
        a = archive_json(self.root / "raw", "training", query, training())
        original = a.read_bytes()
        b = archive_json(self.root / "raw", "training", query, training("new"))
        self.assertNotEqual(a, b)
        self.assertEqual(a.read_bytes(), original)
        self.assertEqual(json.loads(a.with_suffix(".request.json").read_text(encoding="utf-8"))["query"], query)

    def test_offline_reindex_recovers_snapshots_and_cursor(self):
        archive_json(self.root / "raw", "training", {"datestr": "2026-11-04", "include_full_data": True}, training())
        archive_json(self.root / "raw", "food", {"start_date": "2026-11-04", "end_date": "2026-11-04", "include_detail": True}, food([food_day()]))
        archive_json(self.root / "raw", "templates", {"cursor": 0, "include_content": True, "limit": 15},
                     template_page(1, [change(1, name="A")], folder={"id": "agent"}))
        data = self.access()
        self.assertEqual(data.reindex_local(), 3)
        self.assertEqual(data.get_templates()["cursor"], 1)
        self.assertEqual(data.get_training("2026-11-04"), training())
        self.assertEqual(len(data.get_food("2026-11-04", "2026-11-04")["res"]["days"]), 1)
        self.assertEqual(data.reindex_local(), 2)  # Template page is already checkpointed.
        self.assertEqual(data._clients, {})

    def test_atomic_state_replaces_json_without_temporary_files(self):
        path = self.root / "state.json"
        atomic_json(path, {"cursor": 1})
        atomic_json(path, {"cursor": 2})
        self.assertEqual(json.loads(path.read_text()), {"cursor": 2})
        self.assertEqual(list(self.root.glob(".state-*.tmp")), [])

    def test_schema_upgrade_keeps_existing_training(self):
        path = self.root / "training.db"
        from db import connect, init_db
        conn = init_db(connect(path))
        normalize_payload(conn, training(), "2026-11-04")
        conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
        conn.commit()
        conn.close()
        data = self.access()
        self.assertEqual(data.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], "2")
        self.assertEqual(data.conn.execute("SELECT count(*) FROM training_session").fetchone()[0], 1)
        self.assertEqual(data.conn.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_future_schema_is_not_downgraded(self):
        conn = sqlite3.connect(self.root / "training.db")
        conn.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT); INSERT INTO meta VALUES('schema_version','99');")
        conn.close()
        with self.assertRaisesRegex(ValueError, "no downgrade"):
            DataAccess(data_dir=self.root)
        conn = sqlite3.connect(self.root / "training.db")
        self.assertEqual(conn.execute("SELECT value FROM meta").fetchone()[0], '99')
        self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='food_day'").fetchone())
        conn.close()

    def test_templates_accept_documented_res_without_success_flag(self):
        payload = template_page(1)
        del payload["success"]
        data = self.access(remote=True, templates=StubClient([payload]))
        self.assertEqual(data.sync_templates()["cursor"], 1)

    def test_live_root_template_schema_can_replay_without_network(self):
        payload = {"schema_version": "agent_personal_templates_v1", "from_cursor": 0,
                   "next_cursor": 1, "current_revision": 1, "has_more": False,
                   "changes": [{"revision": 1, "entity_type": "folder", "entity_id": "test-folder",
                                "operation": "upsert", "data": {"unique_id": "test-folder", "name": "Agent 模版"}}],
                   "folder": {"unique_id": "test-folder", "name": "Agent 模版"},
                   "limits": {"templates_per_folder": 14}}
        path = archive_json(self.root / 'raw', 'templates',
                            {"cursor": 0, "limit": 15, "include_content": True}, payload)
        before = path.read_bytes()
        data = self.access()
        self.assertEqual(data.reindex_local(domain="templates"), 1)
        state = data.get_templates()
        self.assertEqual(state["cursor"], 1)
        self.assertEqual(state["folder"]["unique_id"], "test-folder")
        self.assertEqual(state["templates"], [])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(data._clients, {})

    def test_live_change_field_names_apply_template_and_delete(self):
        first = {"schema_version": "agent_personal_templates_v1", "from_cursor": 0,
                 "next_cursor": 1, "has_more": False,
                 "changes": [{"revision": 1, "entity_type": "template", "entity_id": "test-template",
                              "operation": "upsert", "data": {"unique_id": "test-template", "name": "A"}}]}
        second = {"schema_version": "agent_personal_templates_v1", "from_cursor": 1,
                  "next_cursor": 2, "has_more": False,
                  "changes": [{"revision": 2, "entity_type": "template", "entity_id": "test-template",
                               "operation": "delete", "data": None}]}
        data = self.access(remote=True, templates=StubClient([first, second]))
        self.assertEqual(len(data.sync_templates()["templates"]), 1)
        self.assertEqual(data.sync_templates()["templates"], [])
        self.assertTrue(data.get_templates(include_deleted=True)["templates"][0]["deleted"])

    def test_root_template_from_cursor_mismatch_preserves_state(self):
        payload = {"schema_version": "agent_personal_templates_v1", "from_cursor": 10,
                   "next_cursor": 11, "has_more": False, "changes": []}
        data = self.access(remote=True, templates=StubClient([payload]))
        with self.assertRaisesRegex(ValueError, "requested cursor"):
            data.sync_templates()
        self.assertIsNone(data.repository.state("templates"))

    def test_unknown_template_operation_does_not_move_cursor(self):
        unknown = change(1, name="A")
        unknown["op"] = "unexpected_operation"
        data = self.access(remote=True, templates=StubClient([template_page(1, [unknown])]))
        with self.assertRaisesRegex(ValueError, "Unknown"):
            data.sync_templates()
        self.assertIsNone(data.repository.state("templates"))

    def test_close_closes_lazy_clients(self):
        client = StubClient([training()])
        with DataAccess(data_dir=self.root, allow_remote=True, clients={"training": lambda: client}) as data:
            data.get_training("2026-11-04")
        self.assertTrue(client.closed)


if __name__ == "__main__":
    unittest.main()
