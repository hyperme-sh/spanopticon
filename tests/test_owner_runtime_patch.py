import copy
import unittest

from scripts.owner_runtime_patch import compare, previous_release, PolicyError


class OwnerRuntimePatchTests(unittest.TestCase):
    def test_latest_same_line_immutable_baseline(self):
        stable = {"tag_name": "v0.1.4", "immutable": True, "prerelease": False, "draft": False}
        other = {**stable, "tag_name": "v0.2.0"}
        older = {**stable, "tag_name": "v0.1.0"}
        test = {**stable, "tag_name": "v0.1.9", "prerelease": True}

        def fetch(path):
            self.assertIn("/releases?", path)
            return [older, test, other, stable]

        self.assertEqual(previous_release("hyperme-sh/user-service", "v0.1.5", fetch), stable)
        with self.assertRaisesRegex(PolicyError, "older"):
            previous_release("hyperme-sh/user-service", "v0.1.3", fetch)
        with self.assertRaisesRegex(PolicyError, "immutable"):
            previous_release("hyperme-sh/user-service", "v0.1.5",
                             lambda _: [{**stable, "immutable": False}])

    def test_preserves_existing_api_and_accepts_qualified_internal_additions(self):
        prior = {
            "openapi": "3.1.0",
            "info": {"title": "owner", "version": "0.1.0"},
            "paths": {"/api/v1/devices/{device}": {"get": {"description": "Read a device"}}},
        }
        patch = copy.deepcopy(prior)
        patch["info"]["version"] = "0.1.1"
        patch["paths"]["/api/v1/internal/identity"] = {"post": {"description": "Resolve owner"}}
        compare(copy.deepcopy(prior), copy.deepcopy(patch))
        changed = copy.deepcopy(patch)
        changed["paths"]["/api/v1/devices/{device}"]["get"]["security"] = []
        with self.assertRaisesRegex(PolicyError, "changes an existing"):
            compare(copy.deepcopy(prior), changed)
        changed = copy.deepcopy(patch)
        changed["paths"]["/api/v1/retire"] = {"post": {}}
        with self.assertRaisesRegex(PolicyError, "adds a public"):
            compare(copy.deepcopy(prior), changed)
