import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from verification import authority, erasure, store, worker


class VerificationErasure(unittest.TestCase):
    def test_restore_with_current_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = root / "secret"
            secret.write_bytes(os.urandom(32))
            state, mailbox = root / "state", root / "mailbox"
            with patch.dict(os.environ, VERIFICATION_KEY_FILE=str(secret), VERIFICATION_DB=str(state),
                            VERIFICATION_MAILBOX=str(mailbox), VERIFICATION_DELIVERY_MODE="local"):
                store.migrate()
                scopes = [store.principal_scope("issuer", tenant, subject) for tenant, subject in
                          [("one", "owner"), ("two", "owner"), ("one", "peer")]]
                ids = []
                for n, scope in enumerate(scopes):
                    cid = store.create(scope, f"user{n}@example.test", "first")["challenge_id"]
                    ids.append(cid)
                    self.assertTrue(worker.deliver_one())
                    self.assertTrue(store.complete(scope, cid, store.delivery_code(store.key(), cid)))
                store.create(scopes[0], "new@example.test", "pending")
                for path in (state, mailbox):
                    shutil.copyfile(path, path.with_suffix(".backup"))
                authority.record(secret, scopes[0])
                authority.record(secret, scopes[0])
                for restored in (False, True):
                    if restored:
                        for path in (state, mailbox):
                            shutil.copyfile(path.with_suffix(".backup"), path)
                    with self.assertRaisesRegex(RuntimeError, "replay required"):
                        store.ready()
                    erasure.replay()
                    erasure.replay()
                    store.ready()
                    self.assertFalse(worker.deliver_one())
                    self.assertFalse(store.evidence(scopes[0])["verified"])
                    self.assertFalse(store.complete(scopes[0], ids[0], store.delivery_code(store.key(), ids[0])))
                    with self.assertRaises(store.RetiredSubject):
                        store.create(scopes[0], "x@example.test", "again")
                    for scope in scopes[1:]:
                        self.assertTrue(store.evidence(scope)["verified"])
                    with sqlite3.connect(str(mailbox)) as db:
                        self.assertEqual(set(db.execute("SELECT id FROM message")), {(i,) for i in ids[1:]})
                    with store.transaction() as db:
                        for table in ("contact", "challenge"):
                            rows = db.execute(f"SELECT scope FROM {table}").fetchall()
                            self.assertCountEqual([row[0] for row in rows], scopes[1:])
