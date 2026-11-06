import asyncio
from dataclasses import replace
import hmac
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from adapter import Adapter, test_verifier
from durable import DurableTestProcessor
from controlplane_billing.settlement import SettlementIntent, SignedSettlementIntent


def signed(intent=None):
    intent = intent or SettlementIntent("wallet", 100, "TEST", "local", "one")
    return SignedSettlementIntent(intent, "signer", hmac.digest(b"test", intent.signing_bytes(), "sha256"))


def submit_in_process(path, queue):
    try:
        result = asyncio.run(DurableTestProcessor(path).submit(signed()))
        queue.put((result.status, result.processor_reference))
    except Exception as exc:
        queue.put(("error", str(exc)))


class DurableTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "ledger.sqlite3"
        self.processor = DurableTestProcessor(self.path)
        self.adapter = Adapter(self.processor, test_verifier({("signer", "wallet"): b"test"}))

    def count(self):
        db = sqlite3.connect(self.path)
        try:
            return db.execute("SELECT count(*) FROM operations").fetchone()[0]
        finally:
            db.close()

    async def test_response_loss_and_restart_recover_committed_operation(self):
        first = await self.adapter.settle(signed())
        # Discard all in-memory state, as after commit followed by lost response.
        restarted = DurableTestProcessor(self.path)
        self.assertEqual(await restarted.submit(signed()), first)
        with self.assertRaisesRegex(ValueError, "conflicting"):
            await restarted.submit(signed(replace(signed().intent, amount=101)))
        self.assertEqual(self.count(), 1)

    async def test_independent_processes_share_one_operation(self):
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        processes = [context.Process(target=submit_in_process, args=(self.path, queue)) for _ in range(4)]
        try:
            for process in processes:
                process.start()
            results = [await asyncio.to_thread(queue.get, timeout=20) for _ in processes]
            for process in processes:
                await asyncio.to_thread(process.join, 20)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(len(set(results)), 1)
            self.assertEqual(results[0][0], "accepted")
            self.assertEqual(self.count(), 1)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join()
            queue.close()

    async def test_invalid_authority_and_real_assets_have_no_effect(self):
        with self.assertRaises(PermissionError):
            await self.adapter.settle(replace(signed(), signature=b"wrong"))
        for intent in (replace(signed().intent, asset="USDC"), replace(signed().intent, network="mainnet")):
            with self.assertRaisesRegex(ValueError, "TEST ledger"):
                await self.adapter.settle(signed(intent))
        self.assertEqual(self.count(), 0)

    async def test_failed_transaction_does_not_poison_next_operation(self):
        await self.adapter.settle(signed())
        with self.assertRaises(ValueError):
            await self.adapter.settle(signed(replace(signed().intent, amount=101)))
        await self.adapter.settle(signed(replace(signed().intent, idempotency_key="two")))
        self.assertEqual(self.count(), 2)
