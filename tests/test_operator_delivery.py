import os
from pathlib import Path
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from verification import operator_delivery, recover_operator, store


class OperatorDeliveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "key").write_bytes(os.urandom(32))
        env = patch.dict(os.environ, VERIFICATION_DB=str(root / "db"), VERIFICATION_KEY_FILE=str(root / "key"),
                         VERIFICATION_DELIVERY_MODE="operator")
        env.start()
        self.addCleanup(env.stop)
        store.migrate()

    def test_timeout_and_crash_do_not_resend(self):
        for name, exception in (("timeout", socket.timeout()), ("crash", SystemExit())):
            cid = store.create(name, name + "@example.test", name)["challenge_id"]
            with patch.object(operator_delivery, "exchange", side_effect=exception) as send:
                if name == "crash":
                    with self.assertRaises(SystemExit):
                        operator_delivery.dispatch_one()
                else:
                    self.assertTrue(operator_delivery.dispatch_one())
                self.assertFalse(operator_delivery.dispatch_one())
                send.assert_called_once()
            self.assertFalse(store.complete(name, cid, store.delivery_code(store.key(), cid)))

    def test_recovery_invalidates_proofs_before_resuming_issuance(self):
        def issue(scope, key):
            cid = store.create(scope, scope + "@example.test", key)["challenge_id"]
            with patch.object(operator_delivery, "exchange", return_value="accepted"):
                self.assertTrue(operator_delivery.dispatch_one())
            return cid, store.delivery_code(store.key(), cid)

        cid, code = issue("verified", "old")
        self.assertTrue(store.complete("verified", cid, code))
        pending, answer = issue("pending", "old")
        recover_operator.invalidate_snapshot_proofs()
        recover_operator.invalidate_snapshot_proofs()
        self.assertFalse(store.evidence("verified")["verified"])
        self.assertFalse(store.complete("pending", pending, answer))
        fresh, answer = issue("pending", "new")
        self.assertTrue(store.complete("pending", fresh, answer))

    def test_subject_limit_and_window_recovery(self):
        with patch.dict(os.environ, VERIFICATION_LIMIT_SUBJECT="1"):
            with patch.object(store.time, "time", return_value=100):
                original = store.create("one", "a@example.test", "first")
                self.assertEqual(original, store.create("one", "a@example.test", "first"))
                with self.assertRaises(store.RateLimited):
                    store.create("one", "b@example.test", "second")
            with patch.object(store.time, "time", return_value=3701):
                self.assertIn("challenge_id", store.create("one", "b@example.test", "second"))

    def test_migration_preserves_existing_local_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, VERIFICATION_DB=str(Path(tmp)/"v1")):
            with sqlite3.connect(os.environ["VERIFICATION_DB"]) as db:
                db.executescript(Path("verification/migrations/001.sql").read_text())
                db.execute("PRAGMA user_version=1")
                db.execute("INSERT INTO contact VALUES ('old',1,'old@example.test',42)")
            store.migrate()
            store.migrate()
            self.assertEqual(store.evidence("old")["method"], "local-test-delivery")
