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

    def test_portal_receives_each_tenant_version_by_id(self):
        cedar, birch = self.catalog["tenants"]
        cedar["tenant_version"] = "1.4.0"
        birch["tenant_version"] = "2.3.1"
        del birch["login"]
        files = render(self.catalog)[cedar["id"]]
        self.assertEqual(json.loads(files["portal.json"]["PORTAL_TENANT_VERSIONS"]), {
            cedar["id"]: "1.4.0", birch["id"]: "2.3.1",
        })
        self.assertNotIn("tenant_version", files["plane.json"])
        birch["tenant_version"] = "test"
        with self.assertRaisesRegex(ValueError, "tenant_version"):
            render(self.catalog)

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

    def test_peer_listener_references_and_pins(self):
        tenant = self.catalog['tenants'][0]
        tenant['network'] = 'hyperme.sh'
        tenant['plane'] = dict(environment='test', repository='hyperme-sh/hypermesh-plane', version='0.2.0', protocol_version=1)
        tenant['peers'] = [dict(url='https://peer.invalid', public_key='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=', plane=tenant['plane'])]
        listener = dict(bind='127.0.0.1', port=8443, cert_file='/run/secrets/peer.crt', key_file='/run/secrets/peer.key', ca_file='/run/secrets/peer-ca.crt')
        tenant['peer_listener'] = listener
        files = render(self.catalog)[tenant['id']]
        self.assertEqual(files['peer-listener.json'], dict(schema_version=1, tenant_id=tenant['id'], peer_pins=[tenant['peers'][0]['public_key']], **listener))
        self.assertNotIn('BEGIN PRIVATE KEY', json.dumps(files))
        for field, bad in [('key_file','-----BEGIN PRIVATE KEY-----'), ('cert_file','relative.crt'), ('ca_file','/run/../key'), ('port',True), ('port',443), ('bind','example.test')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                tenant['peer_listener'] = dict(listener, **{field: bad})
                render(self.catalog)
        tenant['peer_listener'] = listener
        for url in ['http://peer.invalid','https://peer.invalid/path']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                tenant['peers'][0]['url'] = url
                render(self.catalog)
