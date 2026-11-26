import io
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
from unittest import TestCase

from deployment import enterprise_bundle as bundle

ROOT = Path(__file__).resolve().parents[1]
TAG = "0.24.0-test.7"
SHA = "a" * 40


class LocalLayout:
    def __init__(self, layout, reference):
        self.root = layout / "blobs/sha256"
        self.repository, self.manifest = reference.split("@")
        self.corrupt_archive = False

    def get(self, kind, identifier, limit):
        if kind == "manifests":
            return self.read(self.manifest), {"Docker-Content-Digest": self.manifest}
        raw = self.read(identifier)
        if self.corrupt_archive and raw.startswith(b"\x1f\x8b"):
            raw += b"x"
        return raw, {}

    def read(self, digest):
        return (self.root / digest.split(":")[1]).read_bytes()


class EnterpriseBundles(TestCase):
    def test_each_preset_contains_only_its_configuration_and_resolves_exact_tag(self):
        for profile in bundle.PROFILES:
            with self.subTest(profile=profile), TemporaryDirectory() as scratch:
                root = Path(scratch)
                first = bundle.package(ROOT / "profiles/enterprise", profile, TAG, SHA, root / "first")
                second = bundle.package(ROOT / "profiles/enterprise", profile, TAG, SHA, root / "second")
                self.assertEqual(first, second)
                client = LocalLayout(root / "first", first)
                manifest = json.loads(client.read(client.manifest))
                data = client.read(manifest["layers"][1]["digest"])
                with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                    self.assertIn(f"compose.{profile}.yaml", archive.getnames())
                    self.assertIn(f"helm/values.{profile}.yaml", archive.getnames())
                    self.assertEqual(len(archive.getnames()), 6)
                selected = bundle.resolve(profile, "test", client)
                self.assertEqual(selected["candidate"], TAG)
                self.assertEqual(selected["bundle_reference"], first)
                self.assertTrue(all(image.endswith(":test") for image in selected["images"].values()))
                with self.assertRaisesRegex(ValueError, "identity differs"):
                    bundle.resolve(profile, "0.24.0-test.8", client)
                client.corrupt_archive = True
                with self.assertRaisesRegex(ValueError, "digest or size differs"):
                    bundle.resolve(profile, TAG, client)

    def test_only_the_main_publication_workflow_can_publish(self):
        with self.assertRaisesRegex(ValueError, "Only the main"):
            bundle.publish(".", {"GITHUB_EVENT_NAME": "pull_request"})
