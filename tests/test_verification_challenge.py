from hashlib import sha256
import hmac
import unittest

from verification.challenge import Attempt, check_code


class ChallengeCompatibility(unittest.TestCase):
    def setUp(self):
        # Existing persisted rows use compact JSON, including the integer revision.
        digest = hmac.new(b"test", b'["id","scope",1,"000042"]', sha256).hexdigest()
        self.row = dict(challenge_id="id", scope="scope", revision=1, state="pending", attempts=0,
                        expires_at=20, expected_digest=digest, key=b"test", submitted_code="000042",
                        current_scope="scope", current_revision=1, now=10, limit=3)

    def test_success_and_replay(self):
        self.assertEqual(check_code(**self.row), Attempt("consumed", 1, True))
        self.assertEqual(check_code(**(self.row | dict(state="consumed", attempts=1))), Attempt("consumed", 1, False))

    def test_ineligible_challenges_do_not_consume_attempts(self):
        for changes in (dict(current_scope="other"), dict(now=20), dict(current_revision=2)):
            self.assertEqual(check_code(**(self.row | changes)), Attempt("pending", 0, False))

    def test_wrong_code_and_missing_key_count_towards_lock(self):
        for changes in (dict(submitted_code="000041"), dict(key=None)):
            self.assertEqual(check_code(**(self.row | changes | dict(attempts=2))), Attempt("locked", 3, False))
