from dataclasses import asdict
import json
import unittest

from examples.settlement.contract import SettlementIntent, SettlementResult


class WireContractTests(unittest.TestCase):
    def test_canonical_signed_bytes_are_stable(self):
        intent = SettlementIntent("wallet\N{SNOWMAN}", 12345678901234567890, "TEST", "local", "replay/one")
        self.assertEqual(intent.signing_bytes(),
            b'{"amount":12345678901234567890,"asset":"TEST","domain":"controlplane.settlement.intent.v1",'
            b'"idempotency_key":"replay/one","network":"local","wallet":"wallet\\u2603"}')

    def test_invalid_atomic_amounts_and_padded_fields_fail(self):
        for amount in (True, 1.0, "1", 0, -1):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                SettlementIntent("wallet", amount, "TEST", "local", "one")
        for wallet in ("", " wallet", "wallet "):
            with self.subTest(wallet=wallet), self.assertRaises(ValueError):
                SettlementIntent(wallet, 1, "TEST", "local", "one")

    def test_receipt_is_explicit_and_reference_is_opaque(self):
        for status in ("accepted", "pending", "refused"):
            self.assertEqual(json.loads(json.dumps(asdict(SettlementResult(status, "opaque")))),
                             {"status": status, "processor_reference": "opaque"})
        with self.assertRaises(ValueError):
            SettlementResult("paid", "opaque")
