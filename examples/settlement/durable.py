"""Durable local TEST ledger. No external rail or real funds are involved."""
from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3

from .contract import SignedSettlementIntent, SettlementResult


class DurableTestProcessor:
    """Commit the simulated operation and replay binding in one transaction.

    This is safe only because the entire TEST effect is this local ledger row.
    A real rail needs its own idempotency/reconciliation protocol; inserting a
    row before making a network call does not make that call exactly once.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("durable TEST ledger requires a file")
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS operations (
                signer TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                payload BLOB NOT NULL,
                reference TEXT NOT NULL,
                PRIMARY KEY (signer, idempotency_key)
            )""")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA synchronous=FULL")
        return db

    async def submit(self, signed: SignedSettlementIntent) -> SettlementResult:
        return await asyncio.to_thread(self._submit, signed)

    def _submit(self, signed: SignedSettlementIntent) -> SettlementResult:
        intent = signed.intent
        if intent.asset != "TEST" or intent.network != "local":
            raise ValueError("TEST ledger accepts only TEST on local")
        payload = intent.signing_bytes()
        key = (signed.signer, intent.idempotency_key)
        db = self._connect()
        try:
            # Serialize independent processes as well as concurrent HTTP calls.
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT payload, reference FROM operations WHERE signer=? AND idempotency_key=?",
                key,
            ).fetchone()
            if previous is not None:
                if previous[0] != payload:
                    raise ValueError("conflicting settlement replay")
                reference = previous[1]
            else:
                reference = "test:" + hashlib.sha256(
                    signed.signer.encode() + b"\0" + payload
                ).hexdigest()
                db.execute("INSERT INTO operations VALUES (?, ?, ?, ?)", (*key, payload, reference))
            db.commit()
            return SettlementResult("accepted", reference)
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
