import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("new_plane", Path(__file__).resolve().parents[1] / "scripts/new_plane.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)
ASSURANCE = {"mode": "kyc", "providers": {"kyc": {"url": "https://payment.example.invalid", "credential_secret": "kyc-client"}}}
IMAGE = "ghcr.io/hyperme-sh/spanopticon/runtime:test"


class GeneratorTests(unittest.TestCase):
    def test_generated_files_are_valid_json_with_same_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            path = generator.generate("example", IMAGE, Path(temp) / "plane", ASSURANCE)
            compose = json.loads((path / "compose.yaml").read_text())
            kube = json.loads((path / "kubernetes.json").read_text())
            deployment = next(i for i in kube["items"] if i["kind"] == "Deployment")
            self.assertEqual(compose["services"]["example"]["image"], IMAGE)
            self.assertEqual(deployment["spec"]["template"]["spec"]["containers"][0]["image"], IMAGE)

    def test_refuses_unapproved_images_and_invalid_names_before_writing(self):
        for name, image in [("1plane", IMAGE), ("../escape", IMAGE), ("Example", IMAGE), ("a" * 64, IMAGE),
                            ("ok", "example:latest"), ("ok", IMAGE + "\n")]:
            with self.subTest(name=name, image=image), tempfile.TemporaryDirectory() as temp:
                target = Path(temp) / "out"
                with self.assertRaises(ValueError):
                    generator.generate(name, image, target, ASSURANCE)
                self.assertFalse(target.exists())

    def test_existing_directory_file_and_symlink_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "directory").mkdir()
            (root / "file").write_text("keep")
            (root / "link").symlink_to(root / "directory", target_is_directory=True)
            for name in ["directory", "file", "link"]:
                with self.subTest(name=name), self.assertRaises(FileExistsError):
                    generator.generate("example", IMAGE, root / name, ASSURANCE)
            self.assertEqual((root / "file").read_text(), "keep")
            self.assertEqual(list((root / "directory").iterdir()), [])

    def test_network_policy_selects_only_the_generated_application(self):
        kube = generator.manifests("example", IMAGE, ASSURANCE)["kubernetes.json"]
        policy = next(i for i in kube["items"] if i["kind"] == "NetworkPolicy")
        deployment = next(i for i in kube["items"] if i["kind"] == "Deployment")
        selector = policy["spec"]["podSelector"]["matchLabels"]
        own_labels = deployment["spec"]["template"]["metadata"]["labels"]
        self.assertTrue(all(own_labels.get(k) == v for k, v in selector.items()))
        unrelated = {"app.kubernetes.io/name": "unrelated"}
        self.assertFalse(all(unrelated.get(k) == v for k, v in selector.items()))

    def test_missing_or_invalid_assurance_never_creates_output(self):
        for config in [None, {}, {"mode": "none", "providers": {}},
                       {"mode": "kyc", "providers": {}},
                       {"mode": "verification", "providers": ASSURANCE["providers"]}]:
            with self.subTest(config=config), tempfile.TemporaryDirectory() as temp:
                target = Path(temp) / "out"
                with self.assertRaises(ValueError):
                    generator.generate("example", IMAGE, target, config)
                self.assertFalse(target.exists())

    def test_provider_matrix_and_required_credentials_in_both_formats(self):
        for mode, names in [("kyc", ["kyc"]),
                            ("verification", ["verification"]),
                            ("both", ["kyc", "verification"])]:
            config = {"mode": mode, "providers": {n: {
                "url": "https://" + n.replace("_", "-") + ".example.invalid",
                "credential_secret": n.replace("_", "-") + "-client"} for n in names}}
            documents = generator.manifests("example", IMAGE, config)
            compose = documents["compose.yaml"]
            service = compose["services"]["example"]
            pod = documents["kubernetes.json"]["items"][1]["spec"]["template"]["spec"]
            env = {e["name"]: e["value"] for e in pod["containers"][0]["env"]}
            self.assertEqual(service["environment"], env)
            self.assertEqual(env["SPANOPTICON_ASSURANCE_MODE"], mode)
            self.assertEqual(len(service["secrets"]), len(names))
            for secret in compose["secrets"].values():
                self.assertIn(":?provide credential file path}", secret["file"])
            self.assertEqual(len([v for v in pod["volumes"] if "secret" in v]), len(names))
            for volume in pod["volumes"]:
                if "secret" in volume:
                    self.assertFalse(volume["secret"]["optional"])
            self.assertEqual("SPANOPTICON_VERIFICATION_URL" in env, "verification" in names)

    def test_provider_config_rejects_plaintext_credentials_and_unsafe_urls(self):
        import copy
        for url in ["http://verification.example.invalid", "https://user:secret@example.invalid",
                    "https://example.invalid?token=secret", "https://example.invalid/#fragment"]:
            config = copy.deepcopy(ASSURANCE)
            config["providers"]["kyc"]["url"] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                generator.manifests("example", IMAGE, config)
        config = copy.deepcopy(ASSURANCE)
        config["providers"]["kyc"]["token"] = "never-serialize"
        with self.assertRaises(ValueError):
            generator.manifests("example", IMAGE, config)
