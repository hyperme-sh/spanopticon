"""Bring-your-own processor adapter; no network or live funds in the double."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
from typing import Protocol

from .contract import SignedSettlementIntent, SettlementResult


class Processor(Protocol):
    async def submit(self, intent: SignedSettlementIntent) -> SettlementResult:
        """Deduplicate canonical intent bytes using signer and idempotency key.

        A real implementation persists this binding atomically with its operation
        and returns the original result after retries, including lost responses.
        """
        ...


class Adapter:
    def __init__(self, processor: Processor, verify):
        self.processor = processor
        self.verify = verify

    async def settle(self, intent: SignedSettlementIntent) -> SettlementResult:
        # Verification includes wallet authority, not just signature validity.
        if not self.verify(intent):
            raise PermissionError("intent signer lacks wallet authority")
        return await self.processor.submit(intent)


class TestProcessor:
    """Process-local double. Deliberately unsuitable for durable settlement."""
    def __init__(self):
        self.operations = {}
        self.lock = asyncio.Lock()

    async def submit(self, intent: SignedSettlementIntent) -> SettlementResult:
        key = (intent.signer, intent.intent.idempotency_key)
        payload = intent.intent.signing_bytes()
        async with self.lock:
            previous = self.operations.get(key)
            if previous is not None:
                original, result = previous
                if original != payload:
                    raise ValueError("conflicting settlement replay")
                return result
            reference = hashlib.sha256(intent.signer.encode() + b"\0" + payload).hexdigest()
            result = SettlementResult("accepted", "test:" + reference)
            self.operations[key] = (payload, result)
            return result


def test_verifier(wallet_keys):
    """Test-only HMAC authority, keyed by (signer, wallet); never live keys."""
    def verify(signed):
        secret = wallet_keys.get((signed.signer, signed.intent.wallet))
        return secret is not None and hmac.compare_digest(
            signed.signature, hmac.digest(secret, signed.intent.signing_bytes(), "sha256")
        )
    return verify
