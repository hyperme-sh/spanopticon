"""Offline command contracts; no container engine or shared fixtures."""
import ast
import inspect
import copy
import json
import subprocess
import unittest
from unittest.mock import Mock, patch
from examples.operator.check_sample import inspect_service_image, main

class ImageIdentityTests(unittest.TestCase):
    def setUp(self):
        self.identity = "sha256:" + "c" * 64
        self.container = {"Image": self.identity, "Config": {
            "Image": "cedar-test-fixture-identity", "Labels": {
                "com.docker.compose.project": "cedar-test-fixture",
                "com.docker.compose.service": "identity"}}}
        self.image = {"Id": self.identity, "Config": {"Labels": {
            "org.opencontainers.image.revision": "source-revision"}}}
        self.run = Mock(return_value="container-id")
    def inspect(self):
        return inspect_service_image(self.run, "identity", "cedar-test-fixture", "source-revision")
    def test_resolves_reference_not_compose_manifest_listing(self):
        with patch("subprocess.check_output", side_effect=[json.dumps([self.container]), json.dumps([self.image])]) as command:
            self.assertEqual(self.inspect(), self.identity)
        self.run.assert_called_once_with("--profile", "host", "ps", "-aq", "identity", capture=True)
        self.assertEqual(command.call_args_list[1].args[0], ["docker", "image", "inspect", "cedar-test-fixture-identity"])
    def test_rejects_moved_reference_and_wrong_revision(self):
        for field in ("identity", "revision"):
            image = copy.deepcopy(self.image)
            if field == "identity": image["Id"] = "sha256:" + "d" * 64
            else: image["Config"]["Labels"]["org.opencontainers.image.revision"] = "wrong"
            with self.subTest(field=field), patch("subprocess.check_output", side_effect=[json.dumps([self.container]), json.dumps([image])]), self.assertRaises(ValueError):
                self.inspect()
    def test_missing_image_has_no_fallback(self):
        with patch("subprocess.check_output", side_effect=[json.dumps([self.container]), subprocess.CalledProcessError(1, "inspect")]), self.assertRaises(subprocess.CalledProcessError):
            self.inspect()
    def test_requires_exact_container_and_owner(self):
        for ids in ("", "first\nsecond"):
            self.run.return_value = ids
            with patch("subprocess.check_output") as command, self.assertRaises(ValueError): self.inspect()
            command.assert_not_called()
        self.run.return_value = "container-id"
        for key in ("com.docker.compose.project", "com.docker.compose.service"):
            container = copy.deepcopy(self.container); container["Config"]["Labels"][key] = "other"
            with patch("subprocess.check_output", return_value=json.dumps([container])) as command, self.assertRaises(ValueError): self.inspect()
            self.assertEqual(command.call_count, 1)
    def test_rejects_nonimmutable_identity(self):
        self.container["Image"] = "moving-tag"
        with patch("subprocess.check_output", return_value=json.dumps([self.container])) as command, self.assertRaises(ValueError): self.inspect()
        self.assertEqual(command.call_count, 1)

    def test_sample_builds_all_images_before_starting_without_rebuilds(self):
        tree = ast.parse(inspect.getsource(main))
        commands = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "run":
                args = [arg.value for arg in node.args if isinstance(arg, ast.Constant)]
                commands.append((node.lineno, args))
        commands.sort()
        builds = [(line, args) for line, args in commands if "build" in args]
        self.assertEqual(len(builds), 1)
        self.assertEqual(set(builds[0][1][3:]), {"identity", "operator", "settlement", "host"})
        starts = [(line, args) for line, args in commands if "up" in args]
        self.assertTrue(starts)
        for line, args in starts:
            self.assertLess(builds[0][0], line)
            self.assertIn("--no-build", args)
        literals = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        self.assertNotIn("--build", literals)
