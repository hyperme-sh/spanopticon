"""Offline native grant route and signature contracts."""
import time
import unittest
from unittest.mock import Mock
from uuid import uuid4
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from intake import service
from intake.native_identity import PREFIX, AUDIENCE, USE
from intake.tests.test_policy import submission


class NativeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tenant, self.subject, self.device = map(str, (uuid4(), uuid4(), uuid4()))
        self.key = Ed25519PrivateKey.generate()
        service.app.state.native_key = self.key.public_key()
        service.app.state.key = self.key.public_key()
        service.app.state.config = dict(native_feedback_enabled=True,
            native_feedback_issuer="https://plane.example.test", issuer="https://plane.example.test",
            algorithm="EdDSA", audience=AUDIENCE, tenants={self.tenant: {"web": "example/app"}})
        self.store = Mock()
        self.store.consent.return_value = {"enabled": False, "revision": 0}
        self.store.submit.return_value = {"state": "pending"}
        service.app.state.store = self.store
        self.client = TestClient(service.app)
        self.addCleanup(self.client.close)
        self.addCleanup(lambda: setattr(service.app.state, "native_key", None))

    def token(self, **changes):
        now = int(time.time())
        claims = dict(iss="https://plane.example.test", aud=AUDIENCE, sub=self.subject,
            tenant_id=self.tenant, device_id=self.device, jti=str(uuid4()), iat=now,
            exp=now+60, token_use=USE) | changes
        return PREFIX + jwt.encode(claims, self.key, algorithm="EdDSA")

    def request(self, method="GET", path="/v1/consent", token=None, **kwargs):
        return self.client.request(method, path, headers={"Authorization": "Bearer " + (token or self.token()),
            "Idempotency-Key": str(uuid4())}, **kwargs)

    def test_scope_and_canonical_identity_reach_existing_store(self):
        self.assertEqual(self.request().status_code, 200)
        p = self.store.consent.call_args.args[0]
        self.assertEqual((p.tenant,p.subject,p.device,p.issuer),
            (self.tenant,self.subject,self.device,"https://plane.example.test"))
        self.assertEqual(self.request("POST", "/v1/reports", json=submission().model_dump()).status_code, 201)
        self.store.submit.assert_called_once()
        self.assertEqual(self.request("PUT", "/v1/consent", json=dict(enabled=True,
            expected_revision=0, notice="human-reports-v1", purpose="product-feedback")).status_code, 200)

    def test_native_grant_never_authorizes_review_export_or_deletion(self):
        for method, path in [("GET", "/v1/reports"), ("GET", "/v1/review"),
                ("GET", "/v1/privacy/export"), ("DELETE", "/v1/privacy/data"),
                ("POST", "/v1/review/" + str(uuid4()) + "/publish")]:
            response = self.request(method, path)
            self.assertEqual(response.status_code, 401)
        self.assertEqual(self.store.mock_calls, [])

    def test_expiry_signature_identity_and_scope_refused(self):
        for changes in [{"exp":1}, {"exp":int(time.time())+120}, {"iat":True},
                {"iat":int(time.time())+30}, {"aud":"other"}, {"aud":[AUDIENCE,"other"]},
                {"iss":"https://wrong.test"}, {"token_use":"review"}, {"jti":"invalid"},
                {"sub":"not-canonical"}, {"device_id":str(uuid4()).upper()}, {"tenant_id":str(uuid4())}]:
            with self.subTest(changes=changes):
                self.assertEqual(self.request(token=self.token(**changes)).status_code, 401)
        token = self.token()
        self.key = Ed25519PrivateKey.generate()
        self.assertEqual(self.request(token=self.token()).status_code, 401)
        self.assertEqual(self.request(token=token[:-10]+"a"*10).status_code, 401)
        self.assertEqual(self.store.mock_calls, [])

    def test_disabled_and_stripped_prefix_fail_closed(self):
        self.assertEqual(self.request(token=self.token()[len(PREFIX):]).status_code, 401)
        service.app.state.config["native_feedback_enabled"] = False
        self.assertEqual(self.request().status_code, 401)
        self.assertEqual(self.store.mock_calls, [])

    def test_membership_and_consent_refusals_are_preserved(self):
        from intake.store import Refusal
        self.store.submit.side_effect = Refusal(403, "Intake membership required")
        self.assertEqual(self.request("POST", "/v1/reports", json=submission().model_dump()).status_code, 403)
        self.store.consent.side_effect = Refusal(403, "Intake membership required")
        self.assertEqual(self.request().status_code, 403)

    def test_key_configuration_defaults_off_and_requires_dedicated_ed25519(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from cryptography.hazmat.primitives import serialization
        from intake.native_identity import load_key
        self.assertIsNone(load_key({}))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "public.pem"
            path.write_bytes(self.key.public_key().public_bytes(serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo))
            with patch.dict("os.environ", {"NATIVE_FEEDBACK_PUBLIC_KEY_FILE": str(path)}):
                self.assertIsNotNone(load_key(service.app.state.config))
                for issuer in (None, "", "http://wrong.test", "https://user@wrong.test", "https://wrong.test?q=1", " https://wrong.test"):
                    with self.assertRaises(ValueError):
                        load_key(service.app.state.config | {"native_feedback_issuer":issuer})
                path.write_text("bad key")
                with self.assertRaises(ValueError): load_key(service.app.state.config)
