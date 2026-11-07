import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from verification import retention, store


class RetentionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.path = root / "state.db"
        (root / "key").write_bytes(os.urandom(32))
        env = patch.dict(os.environ, VERIFICATION_DB=str(self.path), VERIFICATION_KEY_FILE=str(root/"key"))
        env.start()
        self.addCleanup(env.stop)
        store.migrate()

    def test_preview_apply_and_repeat_preserve_evidence_and_limits(self):
        with patch("time.time", return_value=100):
            for name in ("old", "evidence", "mixed"):
                store.create(name, name+"@example.test", "old-key")
            with store.transaction() as db:
                db.execute("UPDATE contact SET verified_until=9000 WHERE scope='evidence'")
                db.execute("UPDATE challenge SET dispatch='ambiguous' WHERE scope='old'")
        with patch("time.time", return_value=5000), patch.dict(os.environ, VERIFICATION_LIMIT_SUBJECT="1"):
            current = store.create("mixed", "new@example.test", "new-key")
            preview = retention.prune(self.path, 3600)
            self.assertEqual((preview["challenge_rows"], preview["contact_rows"]), (3, 1))
            applied = retention.prune(self.path, 3600, apply=True)
            self.assertEqual(applied, preview | {"applied": True})
            self.assertEqual(retention.prune(self.path, 3600, apply=True)["challenge_rows"], 0)
            self.assertTrue(store.evidence("evidence")["verified"])
            self.assertEqual(store.create("mixed", "new@example.test", "new-key"), current)
            with self.assertRaises(store.RateLimited):
                store.create("mixed", "second@example.test", "another-key")
            with store.transaction() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM contact WHERE scope='old'").fetchone()[0], 0)
        with patch("time.time", return_value=12600):
            self.assertEqual(retention.prune(self.path, 3600, apply=True)["contact_rows"], 2)

    def test_refuses_unsafe_window_or_unknown_schema(self):
        with self.assertRaises(ValueError):
            retention.prune(self.path, 3599, apply=True)
        with store.transaction() as db:
            db.execute("PRAGMA user_version=3")
        with self.assertRaises(ValueError):
            retention.prune(self.path, 3600, apply=True)
