import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from examples.operator import host
from examples.operator.common import initialize


class EnrollmentStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name) / "host.json"
        self.bootstrap = {"tenant_id": "test-tenant", "enroll_token": "test-enroll-token"}

    def test_failed_initial_persistence_never_submits_enrollment(self):
        with patch.object(host, "save_identity", side_effect=OSError("disk full")):
            calls = []
            with self.assertRaises(OSError):
                host.recover_identity(self.state, self.bootstrap, lambda *args: calls.append(args))
            self.assertEqual(calls, [])

    def test_failed_acknowledgment_save_reuses_durable_identity(self):
        requests = []
        def submit(path, body):
            requests.append(body)
            return {"device_id": "device-one", "device_secret": body["device_secret"], "role": "host"}
        original = host.save_identity
        def fail_ack(state, identity):
            if not identity["enrollment_pending"]:
                raise OSError("crash before acknowledgment persisted")
            original(state, identity)
        with patch.object(host, "save_identity", side_effect=fail_ack):
            with self.assertRaises(OSError):
                host.recover_identity(self.state, self.bootstrap, submit)
        pending = json.loads(self.state.read_text())
        self.assertTrue(pending["enrollment_pending"])
        recovered = host.recover_identity(self.state, self.bootstrap, submit)
        self.assertEqual(requests[0], requests[1])
        self.assertEqual(recovered["wg_private_key"], pending["wg_private_key"])
        self.assertFalse(recovered["enrollment_pending"])
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("enroll_token", recovered)

    def test_changed_bootstrap_cannot_replace_pending_identity(self):
        def lost_response(*args):
            raise OSError("lost response")
        with self.assertRaises(OSError):
            host.recover_identity(self.state, self.bootstrap, lost_response)
        before = self.state.read_bytes()
        with self.assertRaises(ValueError):
            host.recover_identity(self.state, self.bootstrap | {"enroll_token": "different"}, lambda *args: self.fail("submitted"))
        self.assertEqual(self.state.read_bytes(), before)

    def test_migration_preserves_existing_enrolled_device(self):
        database = Path(self.directory.name) / "operator.sqlite3"
        db = sqlite3.connect(database)
        db.execute("CREATE TABLE devices(id TEXT PRIMARY KEY, enroll_hash TEXT, secret_hash TEXT)")
        db.execute("INSERT INTO devices VALUES('existing',NULL,'existing-credential-hash')")
        db.commit()
        db.close()
        with patch.dict(os.environ, {"OPERATOR_DB": str(database)}):
            initialize()
            initialize()
        db = sqlite3.connect(database)
        try:
            self.assertEqual(db.execute("SELECT id,enroll_hash,secret_hash,enrollment_receipt_hash FROM devices").fetchone(),
                             ("existing", None, "existing-credential-hash", None))
        finally:
            db.close()
