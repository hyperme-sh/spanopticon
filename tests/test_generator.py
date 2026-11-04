import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("new_plane", Path(__file__).resolve().parents[1] / "scripts/new_plane.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)
IMAGE = "registry.example.org/team/plane@sha256:" + "a" * 64


class GeneratorTests(unittest.TestCase):
    def test_generated_files_are_valid_json_with_same_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            path = generator.generate("example", IMAGE, Path(temp) / "plane")
            compose = json.loads((path / "compose.yaml").read_text())
            kube = json.loads((path / "kubernetes.json").read_text())
            deployment = next(i for i in kube["items"] if i["kind"] == "Deployment")
            self.assertEqual(compose["services"]["example"]["image"], IMAGE)
            self.assertEqual(deployment["spec"]["template"]["spec"]["containers"][0]["image"], IMAGE)

    def test_refuses_mutable_images_and_invalid_names_before_writing(self):
        for name, image in [("../escape", IMAGE), ("Example", IMAGE), ("a" * 64, IMAGE),
                            ("ok", "example:latest"), ("ok", IMAGE + "\n")]:
            with self.subTest(name=name, image=image), tempfile.TemporaryDirectory() as temp:
                target = Path(temp) / "out"
                with self.assertRaises(ValueError):
                    generator.generate(name, image, target)
                self.assertFalse(target.exists())

    def test_existing_directory_file_and_symlink_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "directory").mkdir()
            (root / "file").write_text("keep")
            (root / "link").symlink_to(root / "directory", target_is_directory=True)
            for name in ["directory", "file", "link"]:
                with self.subTest(name=name), self.assertRaises(FileExistsError):
                    generator.generate("example", IMAGE, root / name)
            self.assertEqual((root / "file").read_text(), "keep")
            self.assertEqual(list((root / "directory").iterdir()), [])

    def test_network_policy_selects_only_the_generated_application(self):
        kube = generator.manifests("example", IMAGE)["kubernetes.json"]
        policy = next(i for i in kube["items"] if i["kind"] == "NetworkPolicy")
        deployment = next(i for i in kube["items"] if i["kind"] == "Deployment")
        selector = policy["spec"]["podSelector"]["matchLabels"]
        own_labels = deployment["spec"]["template"]["metadata"]["labels"]
        self.assertTrue(all(own_labels.get(k) == v for k, v in selector.items()))
        unrelated = {"app.kubernetes.io/name": "unrelated"}
        self.assertFalse(all(unrelated.get(k) == v for k, v in selector.items()))
