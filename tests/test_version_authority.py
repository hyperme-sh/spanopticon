"""Version validation is offline and never needs a prior release."""
import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'scripts/check-version-policy.py'
spec = importlib.util.spec_from_file_location('version_authority', SCRIPT)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

class VersionAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        (self.root / '.github').mkdir()
        self.config = {'schema': 1, 'versions': {'pyproject.toml': '0.23.0'},
                       'owned_dependency_versions': {'controlplane-db': '0.1.0'}}
        self.write_policy()
        (self.root / 'pyproject.toml').write_text('[project]\nname = "component"\nversion = "0.23.0"\n')

    def write_policy(self):
        (self.root / '.github/version-policy.json').write_text(json.dumps(self.config))

    def lock(self, name, version):
        (self.root / 'uv.lock').write_text(f'[[package]]\nname = "{name}"\nversion = "{version}"\n')
        subprocess.run(['git', 'add', 'uv.lock'], cwd=self.root, check=True)

    def test_first_build_needs_neither_release_nor_baseline_nor_network(self):
        policy.check(self.root)
        self.assertFalse((self.root / 'releases').exists())

    def test_unauthorized_patch_and_major_are_rejected(self):
        for version in ['0.23.1', '0.24.0', '1.0.0']:
            with self.subTest(version=version):
                (self.root / 'pyproject.toml').write_text(f'[project]\nversion = "{version}"\n')
                with self.assertRaisesRegex(ValueError, 'explicit owner authorization'):
                    policy.check(self.root)

    def test_policy_cannot_approve_stable_line_by_changing_one_number(self):
        self.config['versions']['pyproject.toml'] = '1.0.0'
        self.write_policy()
        (self.root / 'pyproject.toml').write_text('[project]\nversion = "1.0.0"\n')
        with self.assertRaisesRegex(ValueError, 'outside pre-0.24.0'):
            policy.check(self.root)

    def test_owned_transitive_dependency_is_checked(self):
        self.lock('controlplane-db', '1.0.0')
        with self.assertRaisesRegex(ValueError, 'differs from authorized'):
            policy.check(self.root)
        self.lock('controlplane-db', '0.1.0')
        policy.check(self.root)

    def test_third_party_versions_are_not_downgraded(self):
        self.lock('sqlalchemy', '2.0.43')
        policy.check(self.root)

if __name__ == '__main__':
    unittest.main()
