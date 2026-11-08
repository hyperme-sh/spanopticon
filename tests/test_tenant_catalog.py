import json
import unittest
from pathlib import Path

from deployment.tenants import render


class TenantCatalogTest(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads((Path(__file__).resolve().parents[1] / "examples/tenants/catalog.json").read_text())

    def test_binding_and_incomplete_broker(self):
        cedar, birch = self.catalog["tenants"]
        cedar["login"]["providers"] = {"google": {"client_id": "incomplete"}}
        del birch["login"]
        files = render(self.catalog)
        portal = files[cedar["id"]]["portal.json"]
        realm = files[cedar["id"]]["realm.json"]
        self.assertEqual(portal["NEXT_PUBLIC_LOGIN_TENANT_ID"], cedar["id"])
        self.assertEqual(realm["displayName"], cedar["name"])
        self.assertEqual(realm["clients"][0]["clientId"], portal["AUTH_KEYCLOAK_ID"])
        self.assertEqual(realm["identityProviders"], [])
        self.assertEqual(set(json.loads(portal["PORTAL_TENANT_ROUTES"])), {cedar["id"]})
        self.assertEqual(set(files[birch["id"]]), {"plane.json"})

    def test_duplicate_uuid(self):
        first, second = self.catalog["tenants"]
        second["id"] = first["id"]
        with self.assertRaises(ValueError):
            render(self.catalog)

    def test_reject_invalid_peer(self):
        self.catalog["tenants"][0]["peers"] = [{"url": "https://peer.example.test", "public_key": "invalid"}]
        with self.assertRaises(ValueError):
            render(self.catalog)
