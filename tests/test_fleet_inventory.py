import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import tarfile
import unittest
from unittest.mock import Mock, patch

from deployment.fleet_inventory import generate, PORTAL_IMAGE, PORTAL_RUNTIME_SETTINGS
from deployment.qualify import portal_qualification_inputs
from deployment.runtime import assurance_settings, compose_check, helm_check
from scripts import resolve_fleet_portal

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = "0.24.0-test.17"


class FleetInventoryTest(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.inventory = json.loads((ROOT / "fleet/services.json").read_text())
        self.owner = {
            "selector": f"{PORTAL_IMAGE}:test",
            "image": f"{PORTAL_IMAGE}@sha256:" + "a" * 64,
            "version": "0.1.0",
            "source_revision": "b" * 40,
        }

    def test_both_engines_use_identical_qualified_build_and_external_secret_refs(self):
        first, second = self.root / "first", self.root / "second"
        generate(self.inventory, self.owner, CANDIDATE, first)
        generate(self.inventory, self.owner, CANDIDATE, second)
        for name in ("test.json", "compose.json", "chart.tgz"):
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
        inventory = json.loads((first / "test.json").read_bytes())
        self.assertEqual(inventory["components"]["portal"]["image"], self.owner["image"])
        self.assertIsNone(inventory["assurance"])
        compose = json.loads((first / "compose.json").read_bytes())
        compose_check(compose, inventory, None)
        with tarfile.open(first / "chart.tgz") as chart:
            helm = chart.extractfile("spanopticon-plane/resources.json").read()
        deployments = helm_check(helm, inventory, None, "fleet-validation")
        pod = deployments[0]["spec"]["template"]["spec"]
        env = {entry["name"]: entry for entry in pod["containers"][0]["env"]}
        self.assertEqual(pod["containers"][0]["image"], self.owner["image"])
        self.assertEqual(pod["imagePullSecrets"], [{"name": "fleet-ghcr"}])
        self.assertEqual(set(env) - {"AUTH_TRUST_HOST", "AUTH_SECRET"}, set(PORTAL_RUNTIME_SETTINGS))
        self.assertEqual(env["AUTH_SECRET"]["valueFrom"]["secretKeyRef"], {"name": "portal-auth", "key": "token"})
        self.assertIn("PORTAL_AUTH_SECRET_FILE", compose["secrets"]["portal-auth"]["file"])
        self.assertNotIn("AUTH_SECRET", compose["services"]["portal"]["environment"])

    @unittest.skipUnless(shutil.which("helm"), "Helm is installed in fleet validation CI")
    def test_generated_chart_passes_helm_template_and_bundle_validation(self):
        output = self.root / "out"
        generate(self.inventory, self.owner, CANDIDATE, output)
        metadata = json.loads((output / "test.json").read_bytes())
        rendered = subprocess.check_output([
            "helm", "template", "fleet-validation", str(output / "chart.tgz"),
            "--namespace", "fleet-validation",
        ])
        self.assertEqual(len(helm_check(rendered, metadata, None, "fleet-validation")), 2)

    def test_missing_assurance_only_allowed_for_the_owner_portal(self):
        for components in (
            {"api": {"image": self.owner["image"]}},
            {"portal": {"image": "ghcr.io/other/portal@sha256:" + "a" * 64}},
            {"portal": {"image": self.owner["image"]}, "api": {}},
        ):
            with self.subTest(components=components), self.assertRaisesRegex(ValueError, "external assurance"):
                assurance_settings(components, None)

    def test_rejects_mismatched_owner_selector_and_nonqualified_reference(self):
        for update in ({"selector": f"{PORTAL_IMAGE}:latest"}, {"image": f"{PORTAL_IMAGE}:test"},
                       {"version": "latest"}, {"source_revision": "a" * 8}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                generate(self.inventory, self.owner | update, CANDIDATE, self.root / "output")
            self.assertFalse((self.root / "output").exists())

    def test_disposable_secret_setup_accepts_only_job_local_ghcr_credentials(self):
        registry = self.root / "config.json"
        registry.write_text('{"auths":{"example.invalid":{"auth":"fixture"}}}')
        with self.assertRaisesRegex(ValueError, "single GHCR pull identity"):
            portal_qualification_inputs(str(self.root), self.root)
        registry.write_text('{"auths":{"ghcr.io":{"auth":"fixture"}}}')
        _, auth_secret, settings = portal_qualification_inputs(str(self.root), self.root)
        self.assertEqual(set(settings), set(PORTAL_RUNTIME_SETTINGS))
        self.assertGreaterEqual(len(auth_secret.read_text()), 32)
        self.assertFalse(any("fixture" in value for value in settings.values()))

    def test_resolves_moving_owner_once_and_records_only_the_checked_digest(self):
        execute = Mock(side_effect=[b"sha256:" + b"a" * 64 + b"\n", b"", b"b" * 40, b"0.1.0\n"])
        result = resolve_fleet_portal.resolve(self.inventory["services"]["portal"], execute)
        self.assertEqual(result, self.owner)
        commands = [call.args[0] for call in execute.call_args_list]
        self.assertIn(self.owner["selector"], commands[0])
        self.assertTrue(all(self.owner["image"] in command for command in commands[1:]))
        self.assertIn("--network", commands[-1])
        self.assertIn("none", commands[-1])

    def test_next_owner_build_regenerates_the_bundle_and_unchanged_build_skips(self):
        registry = Mock()
        registry.optional_manifest.return_value = "sha256:" + "d" * 64
        verified = {"bundle_reference": resolve_fleet_portal.REPOSITORY + "@" + registry.optional_manifest.return_value}
        previous = {key: value for key, value in self.owner.items() if key != "selector"}
        output = self.root / "out"
        with patch.object(resolve_fleet_portal, "fetch_test", return_value=verified), \
             patch.object(resolve_fleet_portal.bundle, "verify", return_value={"components": {"portal": previous}, "source_revision": "f" * 40}), \
             patch.object(resolve_fleet_portal, "resolve") as resolve:
            resolve.return_value = self.owner
            self.assertFalse(resolve_fleet_portal.reconcile(self.inventory, output, self.root, "17", "f" * 40, registry))
            resolve.return_value = self.owner | {"image": f"{PORTAL_IMAGE}@sha256:" + "e" * 64}
            self.assertTrue(resolve_fleet_portal.reconcile(self.inventory, output, self.root, "17", "f" * 40, registry))
        component = json.loads((output / "test.json").read_text())["components"]["portal"]
        self.assertEqual(component["image"], f"{PORTAL_IMAGE}@sha256:" + "e" * 64)
