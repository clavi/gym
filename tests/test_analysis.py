"""Offline T1--T10 acceptance tests. Only synthetic data and private temp DBs."""
import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from analysis_metrics import calculate, food_pair, volume_summary, merge_volume_summaries
from analysis_run import create_run, read_json, replay, verify
from analysis_semantics import interpret
from analysis_sources import training_sources, food_sources, body_sources
from db import connect, connect_readonly, init_db
from export import preserve_discussion, render
from normalize import normalize_payload
from storage import Repository, archive_json

DAY = "2026-10-26"


def segment(**kwargs):
    return {"weight": "20", "unit": "kg", "reps": "10", "done": True, "load_role": "external", **kwargs}


def payload(sets=None, *, moves=None, day=DAY):
    return {"res": {"trains": [{"datestr": day, "localid": 1, "title": "训练",
                                 "movements": moves if moves is not None else [{"name": "卧推", "sets": sets if sets is not None else [segment()]}]}]}}


def evidence(p, day=DAY, known=True):
    return {"date": day, "status": "synced_records" if p["res"]["trains"] else "synced_empty",
            "projection_matches": known, "sha256": "synthetic-" + day, "payload": p}


def summary(p, *, rules=None, known=True):
    facts = interpret(p, DAY, "synthetic", rules)
    src = evidence(p)
    if not known:
        src["status"] = "coverage_unknown"
    return volume_summary(facts["segments"], facts["other"], [src]), facts


class SemanticTests(unittest.TestCase):
    def test_T7_units_and_load_roles(self):
        for obj, expected, status in (
            (segment(), 200, "complete"),
            (segment(unit="lb"), 200 * .45359237, "complete"),
            (segment(weight_kg=20, unit="lb"), 200, "complete"),
            (segment(unit=None), None, "unverified_semantics"),
            (segment(unit="档"), None, "unverified_semantics"),
            (segment(time=30, movement_type="timed"), None, "not_applicable"),
            (segment(selfWeight=True), None, "not_applicable"),
            (segment(selfWeight=True, extraWeight=True), 200, "complete"),
            (segment(assisted=True), None, "not_applicable")):
            with self.subTest(obj=obj):
                v, facts = summary(payload([obj]))
                self.assertEqual(v["status"], status)
                if expected is None:
                    self.assertIsNone(v["value"])
                else:
                    self.assertAlmostEqual(v["value"], expected, delta=1e-10)
                self.assertEqual(facts["segments"][0]["laterality"], "unknown")
        unverified = segment()
        unverified.pop("load_role")
        self.assertEqual(summary(payload([unverified]))[0]["status"], "unverified_semantics")

    def test_T8_ordinary_drop_and_superset(self):
        drop = {"done": True, "dropSets": [segment(weight=30), segment(weight=20), segment(weight=10)], "rpe": 9}
        v, f = summary(payload([drop]))
        self.assertEqual(v["value"], 600)
        self.assertEqual((len(f["segments"]), len(f["groups"])), (3, 1))
        self.assertEqual(f["groups"][0]["state"], "complete")
        self.assertEqual(len({s["key"] for s in f["segments"]}), 3)
        with_parent = {**segment(weight=40, rpe=9), "dropSets": [segment(weight=20), segment(weight=10)]}
        v, f = summary(payload([with_parent]))
        self.assertEqual(v["value"], 700)
        self.assertIsNone(f["segments"][0]["rpe"])
        sup = {"done": True, "items": [{"name": "卧推", "set": segment()}, {"name": "划船", "set": segment()}]}
        v, f = summary(payload([sup]))
        self.assertEqual((len(f["segments"]), len(f["groups"]), sum(r["complete"] for r in f["rounds"])), (2, 2, 1))
        drop["dropSets"][1]["done"] = False
        _, f = summary(payload([drop]))
        self.assertEqual(f["groups"][0]["state"], "conflict")
        drop.pop("done")
        _, f = summary(payload([drop]))
        self.assertEqual(f["groups"][0]["state"], "partial")
        drop["dropSets"][1].pop("done")
        _, f = summary(payload([drop]))
        self.assertEqual(f["groups"][0]["state"], "unknown")

    def test_T8_warmup_and_unsupported_nested(self):
        v, f = summary(payload([{**segment(setType="热"), "dropSets": [segment()]}]))
        self.assertEqual((v["value"], v["status"]), (0, "no_completed_work"))
        self.assertTrue(all(s["warmup"] for s in f["segments"]))
        v, f = summary(payload([{"items": [{"name": "卧推", "set": {"dropSets": [segment()]}}]}]))
        self.assertEqual(v["status"], "unverified_semantics")
        self.assertFalse(f["groups"])

    def test_T9_all_statuses_and_invalid_fields(self):
        cases = [({"res": {"trains": []}}, 0, "no_training"),
                 (payload([segment(done=False)]), 0, "no_completed_work"),
                 (payload([segment(setType="热")]), 0, "no_completed_work"),
                 (payload(moves=[{"name": "有氧", "cardio": True, "metrics": {"distance": 10}}]), None, "not_applicable"),
                 (payload([segment(selfWeight=True)]), None, "not_applicable"),
                 (payload([segment(weight=0)]), None, "unverified_semantics"),
                 (payload([segment(weight=None)]), None, "insufficient_data"),
                 (payload([segment(unit=None)]), None, "unverified_semantics"),
                 (payload([segment(), segment(weight=None)]), 200, "partial"),
                 (payload(), 200, "complete")]
        for p, value, status in cases:
            with self.subTest(p=p):
                v, _ = summary(p)
                self.assertEqual((v["value"], v["status"]), (value, status))
        for field, value in (("reps", 0), ("reps", -1), ("weight", -1), ("weight", float("nan")),
                             ("weight", float("inf")), ("weight", "10kg"), ("reps", "wrong")):
            with self.subTest(field=field, value=value):
                v, _ = summary(payload([segment(**{field: value})]))
                self.assertEqual(v["status"], "insufficient_data")
                self.assertEqual(v["reasons"]["invalid_fields"], 1)
        v, _ = summary(payload([segment(weight=0)]), rules={"卧推": {"zero_weight": "no_external_load", "evidence": "synthetic confirmation"}})
        self.assertEqual(v["status"], "not_applicable")
        v, _ = summary({"res": {"trains": []}}, known=False)
        self.assertEqual((v["value"], v["status"]), (None, "coverage_unknown"))

    def test_T9_merge_is_partition_invariant(self):
        empty = {"res": {"trains": []}}
        cardio = payload(moves=[{"name": "有氧", "cardio": True}])
        for left, right, expected in ((empty, cardio, (None, "not_applicable")),
                                       (empty, payload(), (200, "complete")),
                                       (payload(), cardio, (200, "partial")),
                                       (payload(), payload([segment(reps=0)]), (200, "partial")),
                                       (empty, empty, (0, "no_training")),
                                       (empty, payload([segment(done=False)]), (0, "no_completed_work"))):
            fa = interpret(left, DAY, "a")
            fb = interpret(right, "2026-10-27", "b")
            sources = [evidence(left), evidence(right, "2026-10-27")]
            direct = volume_summary(fa["segments"] + fb["segments"], fa["other"] + fb["other"], sources)
            partitions = [(fa, [sources[0]]), (fb, [sources[1]])]
            combined = volume_summary([s for f, _ in partitions for s in f["segments"]],
                                      [s for f, _ in partitions for s in f["other"]],
                                      [s for _, cov in partitions for s in cov])
            self.assertEqual(direct, combined)
            self.assertEqual(direct, merge_volume_summaries([
                volume_summary(fa["segments"], fa["other"], [sources[0]]),
                volume_summary(fb["segments"], fb["other"], [sources[1]])]))
            self.assertEqual((direct["value"], direct["status"]), expected)

    def test_T10_zero_and_signed_differences(self):
        self.assertEqual(food_pair(100, 0)["delta_reason"], "target_zero_unknown")
        p = food_pair(100, 0, {"state": "valid_zero", "evidence": "confirmed synthetic"})
        self.assertEqual(p["delta"], 100)
        self.assertIsNone(p["ratio"])
        self.assertIsNone(food_pair(100, 0, {"state": "unset"})["delta"])
        self.assertIsNone(food_pair(None, 100)["delta"])
        self.assertEqual(food_pair(90, 100)["delta"], -10)
        self.assertEqual(food_pair(110, 100)["delta"], 10)

    def test_parent_completion_is_never_copied_to_children(self):
        child = segment()
        child.pop("done")
        v, f = summary(payload([{"done": True, "dropSets": [child]}]))
        self.assertIsNone(f["segments"][0]["done"])
        self.assertEqual(f["groups"][0]["state"], "unknown")
        self.assertEqual(v["status"], "unverified_semantics")

    def test_elapsed_time_and_unknown_exetype_do_not_prove_timed_exercise(self):
        for extra in ({"time": 0}, {"time": 90, "timeLabel": ""}, {"exetype": "undocumented"}):
            with self.subTest(extra=extra):
                v, f = summary(payload([segment(**extra)]))
                self.assertEqual(v["status"], "unverified_semantics")
                self.assertEqual(f["segments"][0]["movement_type"], "unknown")
        v, _ = summary(payload([segment(time=90)]), rules={"卧推": {
            "movement_type": "resistance", "evidence": "synthetic field contract: time is elapsed"}})
        self.assertEqual((v["value"], v["status"]), (200, "complete"))

    def test_unknown_warmup_and_truncation_block_complete_metrics(self):
        v, _ = summary(payload([segment(setType="unknown")]))
        self.assertEqual(v["status"], "unverified_semantics")
        p = payload()
        p["res"]["trains"][0]["movements"][0]["truncated"] = True
        v, _ = summary(p)
        self.assertEqual((v["value"], v["status"]), (200, "partial"))
        v, _ = summary(payload([segment(metrics={"distance": 10})]))
        self.assertEqual((v["value"], v["status"]), (200, "partial"))

    def test_native_cardio_not_completed_and_mixed_unknown(self):
        v, _ = summary(payload(moves=[{"name": "有氧", "cardio": True, "done": False}]))
        self.assertEqual((v["value"], v["status"]), (0, "no_completed_work"))
        empty = volume_summary([], [], [evidence({"res": {"trains": []}})])
        unknown = volume_summary([], [], [{"status": "coverage_unknown"}])
        v = merge_volume_summaries([empty, unknown])
        self.assertEqual((v["value"], v["status"]), (None, "coverage_unknown"))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gym-analysis-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.conn = init_db(connect(self.root / "training.db"))
        self.addCleanup(self.conn.close)

    def remember(self, p, day=DAY):
        query = {"datestr": day, "include_full_data": True}
        path = archive_json(self.root / "raw", "training", query, p)
        Repository(self.conn).remember("training", query, p, path)
        return path

    def task(self, **kwargs):
        return {"start": DAY, "end": "2026-11-01", "questions": ["training.recorded_counts"],
                "modules": ["training"], "as_of": "2026-11-07", **kwargs}

    def test_T1_T2_T3_T5_report_and_rpe(self):
        p = payload([segment(rpe=8), segment(done=False, rpe=10), segment(setType="热", weight=100, rpe=10)])
        p["res"]["trains"].append({**copy.deepcopy(p["res"]["trains"][0]), "localid": 2})
        self.remember(p)
        sources = training_sources(self.conn, self.root / "raw", DAY, "2026-11-01")
        result = calculate(self.conn, sources, self.task())
        m = {x["metric_id"]: x for x in result["metrics"]}
        self.assertEqual(m["training.sessions"]["value"], 2)
        self.assertEqual(m["training.days"]["value"], 1)
        self.assertEqual(m["training.rpe_mean"]["value"], 8)
        self.assertEqual(m["training.rpe_mean"]["sample_n"], 2)
        self.assertEqual(m["training.max_weight"]["value"], 20)
        text = render(self.conn, 2026, 44, raw_root=self.root / "raw")
        self.assertIn("训练次数：2；训练天数：1", text)
        self.assertIn("仅描述", text)
        self.assertNotIn("高于此前", text)

    def test_real_shaped_elapsed_field_only_blocks_dependent_metrics(self):
        self.remember(payload([segment(time=0, timeLabel="", rpe=8)]))
        sources = training_sources(self.conn, self.root / "raw", DAY, DAY)
        result = calculate(self.conn, sources, {**self.task(), "end": DAY})
        m = {x["metric_id"]: x for x in result["metrics"]}
        self.assertEqual(m["training.sessions"]["value"], 1)
        self.assertEqual(m["training.completed_segments"]["value"], 1)
        self.assertEqual(m["training.volume"]["status"], "unverified_semantics")
        self.assertIsNone(m["training.work_sets"]["value"])
        self.assertIsNone(m["training.rpe_mean"]["value"])
        self.assertIsNone(m["training.rpe_coverage"]["value"])
        self.assertTrue(any(e["reason"] == "type_unknown" for e in result["exclusions"] if e["metric_id"] == "training.rpe_mean"))

    def test_T4_valid_latest_and_errors_are_not_rest(self):
        path = self.remember(payload())
        empty = self.remember({"res": {"trains": []}})
        sources = training_sources(self.conn, self.root / "raw", DAY, DAY)
        self.assertEqual(sources[0]["status"], "synced_empty")
        self.assertEqual(sources[0]["raw_path"], str(empty.resolve()))
        empty.write_text('{"success":false,"res":{"trains":[]}}', encoding="utf-8")
        sources = training_sources(self.conn, self.root / "raw", DAY, DAY)
        self.assertNotEqual(sources[0]["status"], "synced_empty")
        path.write_text('{"res":{}}', encoding="utf-8")
        sources = training_sources(self.conn, self.root / "raw", DAY, DAY)
        self.assertEqual(sources[0]["status"], "coverage_unknown")

    def test_T6_freeze_replay_and_tamper(self):
        self.remember(payload())
        before = (self.root / "training.db").read_bytes()
        run = create_run(self.task(daily=True), data_dir=self.root)
        self.assertEqual((self.root / "training.db").read_bytes(), before)
        self.assertEqual(verify(run)["checks"]["replay"], "passed")
        for p in (self.root / "raw").rglob("*.json"):
            p.unlink()  # synthetic fixtures only: prove replay doesn't need Raw
        self.conn.execute("DELETE FROM training_session")
        self.conn.commit()
        replay(run)
        (run / "metrics.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Frozen artifact mismatch"):
            verify(run)

    def test_readonly_does_not_create_missing_db(self):
        with self.assertRaises(sqlite3.OperationalError):
            connect_readonly(self.root / "missing.sqlite")
        self.assertFalse((self.root / "missing.sqlite").exists())

    def test_backup_includes_committed_wal_and_never_initializes_input(self):
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.remember(payload())
        run = create_run(self.task(), data_dir=self.root)
        with sqlite3.connect(run / "source.sqlite") as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM training_session").fetchone()[0], 1)
        c.close()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='analysis_result'").fetchone()[0], 0)

    def test_J1_food_coverage_daily_join_and_optional_domains(self):
        self.remember(payload())
        days = [{"datestr": DAY, "totals": {"totalCal": 100, "totalProtein": 10},
                 "foods": {"records": [{"name": "测试食物", "amount": 100, "unit": "g"}], "limits": {"protein": 10}}}]
        q = {"start_date": DAY, "end_date": "2026-11-01", "include_detail": True}
        p = {"success": True, "res": {"days": days}}
        raw = archive_json(self.root / "raw", "food", q, p)
        Repository(self.conn).remember("food", q, p, raw)
        fs = food_sources(self.conn, self.root / "raw", DAY, "2026-11-01")
        self.assertEqual(fs[0]["status"], "synced_records")
        self.assertEqual(fs[-1]["status"], "synced_empty")
        run = create_run(self.task(modules=["training", "food"], questions=["J1"]), data_dir=self.root)
        replay(run)
        m = read_json(run / "metrics.json")
        intake = next(x for x in m["metrics"] if x["metric_id"] == "food.recorded_day_mean" and x["group"]["nutrient"] == "calories")
        self.assertEqual((intake["value"], intake["sample_n"]), (100, 1))
        self.assertEqual(m["eligibility"][0]["status"], "descriptive_only")
        self.assertTrue((run / "daily.csv").is_file())

    def test_body_page_chain_requires_final_page_and_matching_records(self):
        records = [{"datestr": DAY, "type": "weight", "value": 70, "unit": "kg"},
                   {"datestr": "2026-10-27", "type": "weight", "value": 71, "unit": "kg"}]
        for offset, record in enumerate(records):
            q = {"start_date": DAY, "end_date": "2026-11-01", "types": ["weight"],
                 "include_records": True, "include_latest": offset == 0, "offset": offset, "limit": 1}
            p = {"success": True, "res": {"records": [record], "has_more": offset == 0}}
            raw = archive_json(self.root / "raw", "body", q, p)
            Repository(self.conn).remember("body", q, p, raw)
            if offset == 0:
                self.assertEqual(body_sources(self.conn, DAY, "2026-11-01")[0]["status"], "incomplete_pages_or_projection_mismatch")
        self.assertEqual(body_sources(self.conn, DAY, "2026-11-01")[0]["status"], "synced_scoped_types")
        run = create_run(self.task(modules=["body"], questions=["body.measurement_delta"]), data_dir=self.root)
        replay(run)
        m = read_json(run / "metrics.json")["metrics"]
        delta = next(x for x in m if x["metric_id"] == "body.measurement_delta")
        self.assertEqual(delta["value"], 1)
        mean = next(x for x in m if x["metric_id"] == "body.window_mean")
        self.assertIsNone(mean["value"])

    def test_force_keeps_user_discussions(self):
        text = render(self.conn, 2026, 44, raw_root=self.root / "raw")
        old = text.replace("## 六、恢复情况\n\n待讨论。", "## 六、恢复情况\n\n用户确认的恢复记录。")
        self.assertIn("用户确认的恢复记录", preserve_discussion(old, text))

    def test_T10_per_nutrient_pair_sets_and_signed_mean(self):
        repo = Repository(self.conn)
        days = []
        for d, intake, target in ((DAY, 110, 100), ("2026-10-27", 90, 100), ("2026-10-28", 100, 0)):
            days.append({"datestr": d, "totals": {"totalCal": intake, "totalProtein": intake},
                         "foods": {"records": [], "limits": {"calories": target, "protein": target}}})
        repo._food({"res": {"days": days}})
        self.conn.commit()
        task = self.task(modules=["food"], questions=["food.recorded_target_delta"],
                         target_validity={"2026-10-28": {"calories": {"state": "valid_zero", "evidence": "synthetic"}}})
        result = calculate(self.conn, [], task)
        m = {(x["metric_id"], x.get("group", {}).get("nutrient")): x for x in result["metrics"]}
        self.assertEqual(m["food.target_delta", "protein"]["value"], 0)
        self.assertEqual(m["food.target_delta", "protein"]["sample_n"], 2)
        self.assertEqual(m["food.target_delta", "calories"]["sample_n"], 3)
        self.assertEqual(m["food.target_delta_ratio", "calories"]["sample_n"], 2)
        self.assertEqual(len(result["daily"]), 7)
        self.assertIsNone(result["daily"][-1]["food_calories_intake"])


if __name__ == "__main__":
    unittest.main()
