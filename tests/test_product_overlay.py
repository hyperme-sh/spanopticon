import unittest

from deployment.product_overlay import validate


class ProductOverlayTest(unittest.TestCase):
    def setUp(self):
        self.overlay = {
            "schema_version": 1, "product": "example.org", "display_name": "Example",
            "plane_overlay": "example", "services": ["plane", "keycloak"],
            "login": {"kind": "shared-issuer", "providers": ["google"], "options": ["siwe", "google"]},
        }

    def test_product_login_contract(self):
        validate(self.overlay)
        self.overlay["login"]["options"].remove("siwe")
        with self.assertRaisesRegex(ValueError, "Missing wallet"):
            validate(self.overlay)

    def test_rejects_unknown_service_version_and_environment_data(self):
        for changes, error in (
            ({"services": ["plane", "unknown"]}, "overlay service"),
            ({"schema_version": 2}, "schema version"),
            ({"tenant_id": "not-for-the-product"}, "overlay fields"),
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, error):
                validate(self.overlay | changes)

    def test_no_login_without_keycloak(self):
        self.overlay["services"] = ["plane"]
        with self.assertRaisesRegex(ValueError, "services differ"):
            validate(self.overlay)
        del self.overlay["login"]
        validate(self.overlay)
