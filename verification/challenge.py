"""Provider-independent verification of a one-time contact challenge.

The caller owns authentication, scope, rate limits and the transaction that
persists the returned attempt count and state before reporting success.
"""
import hashlib
import hmac
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Attempt:
    state: str
    attempts: int
    accepted: bool


def code_digest(key: bytes, challenge_id: str, scope: str, revision: int, code: str) -> str:
    message = json.dumps([challenge_id, scope, revision, code], separators=(",", ":")).encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def check_code(*, challenge_id, scope, revision, state, attempts, expires_at,
               expected_digest, key, submitted_code, current_scope, current_revision,
               now, limit):
    """Evaluate one attempt against the locked, current challenge snapshot."""
    if (scope != current_scope or revision != current_revision or state != "pending"
            or now >= expires_at or not 0 <= attempts < limit):
        return Attempt(state, attempts, False)
    attempts += 1
    if key and hmac.compare_digest(expected_digest, code_digest(key, challenge_id, scope, revision, submitted_code)):
        return Attempt("consumed", attempts, True)
    return Attempt("locked" if attempts == limit else "pending", attempts, False)
