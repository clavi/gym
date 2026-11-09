"""Sync state checkpoints and argument validation, with all clients stubbed."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from access import DataAccess
import sync_training


class TrainingStub:
    def __init__(self, fail_day=None):
        self.fail_day = fail_day
        self.days = []

    def get_training(self, datestr, **kwargs):
        self.days.append(datestr)
        if datestr == self.fail_day:
            raise RuntimeError("simulated interruption")
        return {"res": {"trains": []}}

    def close(self):
        pass


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gym-sync-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = TrainingStub()
        self.addCleanup(patch.stopall)
        patch.object(sync_training, "SYNC_STATE_PATH", self.root / "sync_state.json").start()
        patch.object(sync_training, "DataAccess", side_effect=lambda **kw: DataAccess(
            data_dir=self.root, clients={"training": lambda: self.client}, **kw)).start()
        self.output = io.StringIO()
        self.redirect = redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def test_checkpoint_is_saved_before_later_request_fails(self):
        self.client.fail_day = "2026-11-05"
        with self.assertRaises(RuntimeError):
            sync_training.sync(start="2026-11-04", end="2026-11-05", refresh_recent=0)
        state = json.loads((self.root / "sync_state.json").read_text())
        self.assertEqual(state["last_training_date"], "2026-11-04")
        self.assertEqual(state["last_fetched"], 1)
        self.client.fail_day = None
        sync_training.sync(start="2026-11-04", end="2026-11-05", refresh_recent=0)
        self.assertEqual(self.client.days, ["2026-11-04", "2026-11-05", "2026-11-05"])
        state = json.loads((self.root / "sync_state.json").read_text())
        self.assertEqual(state["last_training_date"], "2026-11-05")
        self.assertEqual(state["last_skipped"], 1)

    def test_invalid_ranges_and_zero_days_do_not_request(self):
        cases = [dict(days=0), dict(days=-1), dict(refresh_recent=-1),
                 dict(days=1, start="2026-11-04"), dict(start="2026-11-05", end="2026-11-04")]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                sync_training.sync(**kwargs)
        self.assertEqual(self.client.days, [])

    def test_refresh_uses_new_snapshots_and_keeps_old_files(self):
        sync_training.sync(start="2026-11-04", end="2026-11-04", refresh_recent=0)
        before = {p: p.read_bytes() for p in (self.root / "raw").rglob("*.json")}
        sync_training.sync(start="2026-11-04", end="2026-11-04", refresh_recent=1)
        self.assertEqual(len(self.client.days), 2)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertEqual(len(list((self.root / "raw").rglob("*.request.json"))), 2)


if __name__ == "__main__":
    unittest.main()
