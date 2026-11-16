"""ASGI contracts with synthetic signing keys and a substituted store."""

import time
import unittest
from unittest.mock import Mock
from uuid import uuid4
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient
from intake import service
from intake.tests.test_policy import submission


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tenant = str(uuid4())
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        service.app.state.key = self.key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        service.app.state.config = {
            "issuer": "https://issuer.test",
            "audience": "feedback",
            "algorithm": "RS256",
            "tenants": {self.tenant: {"web": "example/app"}},
            "publish_enabled": False,
        }
        self.store = Mock()
        service.app.state.store = self.store
        self.store.submit.return_value = {"state": "pending"}
        self.client = TestClient(service.app)

    def token(self, **overrides):
        claims = (
            dict(
                sub="machine",
                device_id="device",
                tenant_id=self.tenant,
                iss="https://issuer.test",
                aud="feedback",
                iat=int(time.time()),
                exp=int(time.time()) + 120,
            )
            | overrides
        )
        return {
            "Authorization": "Bearer "
            + jwt.encode(claims, self.key, algorithm="RS256"),
            "Idempotency-Key": str(uuid4()),
        }

    def test_machine_claims_are_bound_not_body_supplied(self):
        result = self.client.post(
            "/v1/reports", headers=self.token(), json=submission().model_dump()
        )
        self.assertEqual(result.status_code, 201)
        self.assertEqual(self.store.submit.call_args.args[0].tenant, self.tenant)
        self.assertEqual(self.store.submit.call_args.args[3], "example/app")

    def test_wrong_tenant_issuer_audience_expiry_and_device(self):
        for overrides in (
            {"tenant_id": str(uuid4())},
            {"iss": "https://attacker.test"},
            {"aud": "other"},
            {"exp": 1},
            {"device_id": []},
            {"sub": ""},
        ):
            with self.subTest(overrides=overrides):
                response = self.client.post(
                    "/v1/reports",
                    headers=self.token(**overrides),
                    json=submission().model_dump(),
                )
                self.assertEqual(response.status_code, 401)
        self.store.submit.assert_not_called()

    def test_missing_auth_is_refused(self):
        self.assertIn(self.client.get("/v1/reports").status_code, (401, 403))

    def test_validation_does_not_echo_payload(self):
        payload = submission().model_dump() | {"prompt": "private-input"}
        response = self.client.post("/v1/reports", headers=self.token(), json=payload)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("private-input", response.text)
        self.store.submit.assert_not_called()

    def test_streamed_body_limit(self):
        response = self.client.post(
            "/v1/reports",
            headers=self.token(),
            content=iter([b"x" * 13000, b"x" * 13000]),
        )
        self.assertEqual(response.status_code, 413)

    def test_unconfigured_destination_and_publish_disabled(self):
        response = self.client.post(
            "/v1/reports",
            headers=self.token(),
            json=submission(component="other").model_dump(),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            self.client.post(
                f"/v1/review/{uuid4()}/publish", headers=self.token()
            ).status_code,
            503,
        )
        self.store.publish.assert_not_called()

    def test_erasure_requires_confirmation(self):
        response = self.client.delete(
            "/v1/privacy/data", headers=self.token() | {"Confirm": "no"}
        )
        self.assertEqual(response.status_code, 422)
        self.store.erase.assert_not_called()
