import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from verification import store, worker


class LocalVerification(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "secret").write_bytes(os.urandom(32))
        env = patch.dict(os.environ, VERIFICATION_DB=str(root/"service.db"),
                         VERIFICATION_KEY_FILE=str(root/"secret"), VERIFICATION_MAILBOX=str(root/"mail.db"))
        env.start()
        self.addCleanup(env.stop)
        store.migrate()

    def test_expiry_and_failed_attempts_commit(self):
        with patch.object(store.time, "time", return_value=100):
            cid = store.create("tenant/owner", "x@example.test", "1")["challenge_id"]
            self.assertTrue(worker.deliver_one())
            self.assertFalse(worker.deliver_one())
            code = store.delivery_code(store.key(), cid)
            wrong = "111111" if code == "000000" else "000000"
            for _ in range(5):
                self.assertFalse(store.complete("tenant/owner", cid, wrong))
            self.assertFalse(store.complete("tenant/owner", cid, code))
            second = store.create("tenant/owner", "x@example.test", "2")["challenge_id"]
            worker.deliver_one()
        with patch.object(store.time, "time", return_value=700):
            self.assertFalse(store.complete("tenant/owner", second, store.delivery_code(store.key(), second)))

    def test_contact_change_invalidates_pending_delivery(self):
        first = store.create("one", "a@example.test", "a")["challenge_id"]
        second = store.create("one", "b@example.test", "b")["challenge_id"]
        self.assertTrue(worker.deliver_one())
        self.assertFalse(worker.deliver_one())
        self.assertFalse(store.complete("one", first, store.delivery_code(store.key(), first)))
        self.assertTrue(store.complete("one", second, store.delivery_code(store.key(), second)))
