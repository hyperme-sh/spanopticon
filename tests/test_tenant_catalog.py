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

    def test_peer_contract(self):
        tenant = self.catalog["tenants"][0]
        tenant["plane"] = dict(environment="test", repository="example/plane", version="0.2.0", protocol_version=1)
        peer = dict(url="https://peer.example.test", public_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                    plane=dict(tenant["plane"], version="0.2.9"))
        tenant["peers"] = [peer]
        self.assertEqual(render(self.catalog)[tenant["id"]]["plane.json"]["peers"], [peer])
        for field, value in [("environment", "production"), ("version", "0.3.0"), ("version", "1.2.0"),
                             ("repository", "other/plane"), ("protocol_version", 2)]:
            with self.subTest(field=field, value=value):
                peer["plane"] = dict(tenant["plane"], **{field:value})
                with self.assertRaisesRegex(ValueError, "Incompatible"):
                    render(self.catalog)

    def test_equivalent_login_authorities_collide(self):
        cedar, birch = self.catalog["tenants"]
        login = birch["login"]
        for field in ("portal_origin", "keycloak_origin"):
            with self.subTest(field=field):
                original = dict(login)
                login[field] = cedar["login"][field].upper() + ":443"
                if field == "keycloak_origin":
                    login["realm"] = cedar["login"]["realm"]
                with self.assertRaisesRegex(ValueError, "Duplicate"):
                    render(self.catalog)
                login.update(original)
