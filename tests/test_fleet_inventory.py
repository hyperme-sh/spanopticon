import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import unittest

import yaml

from deployment.fleet_inventory import PORTAL_IMAGE, PORTAL_RUNTIME_SETTINGS, generate

ROOT = Path(__file__).resolve().parents[1]


class FleetInventoryTest(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.inventory = json.loads((ROOT / "deployment/enterprise-services.json").read_text())

    def test_both_engines_preserve_the_channel_and_external_secret_refs(self):
        self.inventory["services"]["portal"]["channel"] = "1.4"
        first = generate(self.inventory, "0.1.0")
        self.assertEqual(first, generate(self.inventory, "0.1.0"))
        compose = json.loads(first["compose.json"])
        portal = compose["services"]["portal"]
        self.assertEqual(portal["image"], f"{PORTAL_IMAGE}:1.4")
        self.assertEqual(portal["pull_policy"], "always")
        self.assertIn("PORTAL_AUTH_SECRET_FILE", compose["secrets"]["portal-auth"]["file"])
        self.assertNotIn("AUTH_SECRET", portal["environment"])

        with tarfile.open(fileobj=io.BytesIO(first["chart.tgz"])) as chart:
            resources = json.load(chart.extractfile("spanopticon-plane/resources.json"))
            template = chart.extractfile("spanopticon-plane/templates/resources.yaml").read()
        self.assertIn(b"$.Release.Revision", template)
        deployment, service = resources["items"]
        self.assertEqual(service["kind"], "Service")
        pod = deployment["spec"]["template"]["spec"]
        self.assertEqual(pod["imagePullSecrets"], [{"name": "fleet-ghcr"}])
        container = pod["containers"][0]
        self.assertEqual(container["image"], portal["image"])
        self.assertEqual(container["imagePullPolicy"], "Always")
        env = {entry["name"]: entry for entry in container["env"]}
        self.assertEqual(set(env) - {"AUTH_TRUST_HOST", "AUTH_SECRET", "TMPDIR"}, set(PORTAL_RUNTIME_SETTINGS))
        inputs = json.loads(first["runtime-inputs.json"])
        self.assertEqual(inputs["settings"]["keys"], list(PORTAL_RUNTIME_SETTINGS))
        self.assertEqual(inputs["secret"]["helm"], env["AUTH_SECRET"]["valueFrom"]["secretKeyRef"])
        self.assertEqual(inputs["secret"]["compose_file_env"], "PORTAL_AUTH_SECRET_FILE")

    def test_rejects_missing_channel_wrong_owner_and_digest_references(self):
        for repo, channel in (
            (PORTAL_IMAGE, ""), (PORTAL_IMAGE, "test@sha256:" + "a" * 64),
            ("ghcr.io/example/other", "test"), (f"{PORTAL_IMAGE}@sha256:" + "a" * 64, "test"),
        ):
            self.inventory["services"]["portal"] = {"repository": repo, "channel": channel}
            with self.subTest(repo=repo, channel=channel), self.assertRaises(ValueError):
                generate(self.inventory, "0.1.0")

    @unittest.skipUnless(os.environ.get("CHECK_ENTERPRISE_RENDER") == "1", "Native render check runs in CI")
    def test_compose_and_helm_render_without_pulling_images(self):
        output = self.root / "out"
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/render_fleet.py"), "--chart-version", "0.1.0", "--output", output.name],
            cwd=self.root, check=True,
        )
        secret = self.root / "fixture-auth"
        secret.write_text("configuration-only")
        env = {**os.environ, **dict.fromkeys(PORTAL_RUNTIME_SETTINGS, "fixture"),
               "PORTAL_AUTH_SECRET_FILE": str(secret)}
        compose = subprocess.check_output(
            ["docker", "compose", "--file", str(output / "compose.json"), "config", "--format", "json"], env=env,
        )
        self.assertEqual(json.loads(compose)["services"]["portal"]["image"], f"{PORTAL_IMAGE}:test")
        for flags in ([], ["--is-upgrade"]):
            helm = subprocess.check_output(
                ["helm", "template", "enterprise-portal", str(output / "chart.tgz"), "--namespace", "portal", *flags],
                text=True,
            )
            self.assertIn(f'"image":"{PORTAL_IMAGE}:test"', helm)
            self.assertIn('"spanopticon.sh/helm-revision":"1"', helm)

    @unittest.skipUnless(os.environ.get("CHECK_ENTERPRISE_RENDER") == "1", "Native render check runs in CI")
    def test_named_configs_render_the_same_selected_services(self):
        enterprise = ROOT / "profiles/enterprise"
        for name in ("s3rch-web", "portal", "payment", "verification"):
            (self.root / f"{name}.env").touch()
        env = {**os.environ, "SPANOPTICON_CONFIG_DIR": str(self.root), "COMPOSE_PROFILES": ""}
        cases = (
            ("s3rch", (), {"s3rch-web"}),
            ("hyperme", (), {"hyperme-web"}),
            ("spanopticon", ("portal",), {"portal"}),
            ("spanopticon", ("portal", "payment", "verification"), {"portal", "payment", "verification"}),
        )
        for name, optional, expected in cases:
            with self.subTest(name=name, optional=optional):
                compose_args = [arg for service in optional for arg in ("--profile", service)]
                helm_args = [arg for service in optional for arg in ("--set", f"services.{service}.enabled=true")]
                compose = subprocess.check_output(
                    ["docker", "compose", "-f", str(enterprise / f"compose.{name}.yaml"),
                     *compose_args, "config", "--format", "json"], env=env,
                )
                helm = subprocess.check_output(
                    ["helm", "template", "replica", str(enterprise / "helm"),
                     "-f", str(enterprise / f"helm/values.{name}.yaml"), *helm_args],
                )
                compose_images = {service: spec["image"] for service, spec in json.loads(compose)["services"].items()}
                containers = (item["spec"]["template"]["spec"]["containers"][0]
                              for item in yaml.safe_load_all(helm) if item and item["kind"] == "Deployment")
                self.assertEqual(set(compose_images), expected)
                self.assertEqual({item["name"]: item["image"] for item in containers}, compose_images)
