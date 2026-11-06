# SPDX-License-Identifier: Apache-2.0
# Public reference implementation of controlplane.settlement.intent.v1.
# Keep wire bytes compatible; see test_contract.py and the billing consumer check.
"""Provider-neutral settlement boundary.

Signatures cover canonical intent bytes. Adapters must verify signer authority
and bind idempotency keys to those bytes before performing any side effect.
Amounts are integer atomic units of the explicitly named asset and network.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Literal, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class SettlementIntent:
    wallet: str
    amount: int
    asset: str
    network: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if type(self.amount) is not int or self.amount <= 0:
            raise ValueError("amount must be a positive integer in atomic units")
        for name in ("wallet", "asset", "network", "idempotency_key"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"{name} must be a nonempty, unpadded string")

    def signing_bytes(self) -> bytes:
        """Domain-separated, deterministic bytes including every intent field."""
        return json.dumps(
            {"domain": "controlplane.settlement.intent.v1", "wallet": self.wallet,
             "amount": self.amount, "asset": self.asset, "network": self.network,
             "idempotency_key": self.idempotency_key},
            sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")


@dataclass(frozen=True, slots=True)
class SignedSettlementIntent:
    intent: SettlementIntent
    signer: str
    signature: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.intent, SettlementIntent):
            raise TypeError("intent must be a SettlementIntent")
        if not isinstance(self.signer, str) or not self.signer.strip():
            raise ValueError("signer is required")
        if not isinstance(self.signature, bytes) or not self.signature:
            raise ValueError("signature bytes are required")


@dataclass(frozen=True, slots=True)
class SettlementResult:
    """Explicit disposition and an opaque processor reference."""
    status: Literal["accepted", "refused", "pending"]
    processor_reference: str

    def __post_init__(self) -> None:
        if self.status not in ("accepted", "refused", "pending"):
            raise ValueError("unknown settlement status")
        if not isinstance(self.processor_reference, str) or not self.processor_reference.strip():
            raise ValueError("processor_reference is required")


@runtime_checkable
class SettlementPort(Protocol):
    async def settle(self, intent: SignedSettlementIntent) -> SettlementResult:
        """Verify authority, reject conflicting replays, return an opaque receipt."""
        ...
