import asyncio
from dataclasses import replace
import hmac
import unittest

from adapter import Adapter, TestProcessor, test_verifier
from controlplane_billing.settlement import SettlementIntent, SignedSettlementIntent


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.processor = TestProcessor()
        self.adapter = Adapter(self.processor, test_verifier({("signer", "wallet"): b"test-secret"}))
        self.intent = SettlementIntent("wallet", 100, "TEST", "local", "one")

    def signed(self, intent=None):
        intent = intent or self.intent
        return SignedSettlementIntent(intent, "signer", hmac.digest(b"test-secret", intent.signing_bytes(), "sha256"))

    async def test_concurrent_replay_has_one_operation(self):
        results = await asyncio.gather(*(self.adapter.settle(self.signed()) for _ in range(10)))
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(results[0].status, "accepted")
        self.assertEqual(len(self.processor.operations), 1)

    async def test_conflicting_replay_refused(self):
        await self.adapter.settle(self.signed())
        with self.assertRaises(ValueError):
            await self.adapter.settle(self.signed(replace(self.intent, amount=101)))
        self.assertEqual(len(self.processor.operations), 1)

    async def test_invalid_signature_and_wrong_wallet_have_no_effect(self):
        for signed in [replace(self.signed(), signature=b"wrong"), self.signed(replace(self.intent, wallet="other"))]:
            with self.assertRaises(PermissionError):
                await self.adapter.settle(signed)
        self.assertFalse(self.processor.operations)
