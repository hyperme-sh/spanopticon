import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('component_release', Path(__file__).resolve().parents[1] / 'scripts/component_release.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class ComponentReleaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.current = self.root / 'current'; self.previous = self.root / 'previous'
        for root in [self.current, self.previous]:
            root.mkdir(); (root / 'pyproject.toml').write_text('[project]\nversion="0.24.0"\n')
        (self.current / 'release-policy.json').write_text(json.dumps({'schema_version': 1,
            'repository': 'example/component', 'component': 'component', 'line': '0.24',
            'version_files': ['pyproject.toml']}))

    def bundle(self, root, folder, ver):
        path = root / 'releases' / folder; path.mkdir(parents=True)
        raw = b'{"public_api":{"read":"str"}}'
        (path / 'api.json').write_bytes(raw)
        component = {'version': ver, 'contract': 'api.json', 'contract_sha256': hashlib.sha256(raw).hexdigest(),
                     'artifact': {'kind': 'package', 'uri': 'https://example.invalid/pkg', 'sha256': 'a'*64}}
        (path / 'release.json').write_text(json.dumps({'schema_version': 1, 'line': '0.24', 'components': {'component': component}}))

    def test_configuration_only_does_not_require_fabricated_baseline(self):
        self.assertFalse(module.release_required(self.current, self.previous, 'example/component', '0.24'))

    def test_version_change_and_candidate_submission_require_admission(self):
        (self.current / 'pyproject.toml').write_text('[project]\nversion="0.24.1"\n')
        self.assertTrue(module.release_required(self.current, self.previous, 'example/component', '0.24'))
        (self.current / 'pyproject.toml').write_text('[project]\nversion="0.24.0"\n')
        self.bundle(self.current, 'candidate', '0.24.0')
        self.assertTrue(module.release_required(self.current, self.previous, 'example/component', '0.24'))

    def test_missing_accepted_baseline_rejects_release(self):
        self.bundle(self.current, 'candidate', '0.24.0')
        with self.assertRaises(ValueError): module.admit(self.current, self.previous, 'example/component', '0.24')

    def test_tag_and_package_versions_bind_candidate(self):
        self.bundle(self.previous, 'accepted', '0.24.0')
        self.bundle(self.current, 'candidate', '0.24.1')
        with self.assertRaisesRegex(ValueError, 'package version'):
            module.admit(self.current, self.previous, 'example/component', '0.24')
        (self.current / 'pyproject.toml').write_text('[project]\nversion="0.24.1"\n')
        self.assertEqual(module.admit(self.current, self.previous, 'example/component', '0.24', 'v0.24.1'), '0.24.1')
        with self.assertRaisesRegex(ValueError, 'tag version'):
            module.admit(self.current, self.previous, 'example/component', '0.24', 'v0.24.2')

    def test_repository_and_line_cannot_be_substituted(self):
        for repo, line in [('other/component', '0.24'), ('example/component', '0.25')]:
            with self.subTest(repo=repo, line=line), self.assertRaises(ValueError):
                module.configuration(self.current, repo, line)

    def test_version_source_cannot_escape_checkout(self):
        config = json.loads((self.current / 'release-policy.json').read_text())
        config['version_files'] = ['../outside/pyproject.toml']
        (self.current / 'release-policy.json').write_text(json.dumps(config))
        with self.assertRaises(ValueError): module.configuration(self.current, 'example/component', '0.24')

    def test_package_move_preserves_identity_and_detects_version_changes(self):
        (self.previous / 'release-policy.json').write_text((self.current / 'release-policy.json').read_text())
        (self.previous / 'pyproject.toml').write_text('[project]\nname="owned"\nversion="0.24.0"\n')
        (self.current / 'pyproject.toml').unlink()
        folder = self.current / 'service'; folder.mkdir()
        moved = folder / 'pyproject.toml'
        moved.write_text('[project]\nname="owned"\nversion="0.24.0"\n')
        config = json.loads((self.current / 'release-policy.json').read_text())
        config['version_files'] = ['service/pyproject.toml']
        (self.current / 'release-policy.json').write_text(json.dumps(config))
        self.assertFalse(module.release_required(self.current, self.previous, 'example/component', '0.24'))
        for name, ver in [('owned', '0.24.1'), ('substitute', '0.24.0')]:
            moved.write_text(f'[project]\nname="{name}"\nversion="{ver}"\n')
            self.assertTrue(module.release_required(self.current, self.previous, 'example/component', '0.24'))

    def test_removing_tracked_package_requires_admission(self):
        (self.previous / 'extra').mkdir()
        (self.previous / 'extra/pyproject.toml').write_text('[project]\nname="extra"\nversion="0.24.0"\n')
        config = json.loads((self.current / 'release-policy.json').read_text())
        config['version_files'].append('extra/pyproject.toml')
        (self.previous / 'release-policy.json').write_text(json.dumps(config))
        self.assertTrue(module.release_required(self.current, self.previous, 'example/component', '0.24'))

    def test_duplicate_package_names_fail_closed(self):
        self.test_package_move_preserves_identity_and_detects_version_changes()
        (self.current / 'pyproject.toml').write_text('[project]\nname="substitute"\nversion="0.24.0"\n')
        config = json.loads((self.current / 'release-policy.json').read_text())
        config['version_files'].append('pyproject.toml')
        (self.current / 'release-policy.json').write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, 'Duplicate package identity'):
            module.release_required(self.current, self.previous, 'example/component', '0.24')

    def test_python_distribution_aliases_cannot_duplicate_identity(self):
        (self.current / 'pyproject.toml').write_text('[project]\nname="Owned_Package"\nversion="0.24.0"\n')
        (self.current / 'extra').mkdir()
        (self.current / 'extra/pyproject.toml').write_text('[project]\nname="owned-package"\nversion="0.24.0"\n')
        config = json.loads((self.current / 'release-policy.json').read_text())
        config['version_files'].append('extra/pyproject.toml')
        (self.current / 'release-policy.json').write_text(json.dumps(config))
        # The prior source is otherwise valid; alias detection must reject the
        # candidate before accepting two spellings as distinct distributions.
        (self.previous / 'extra').mkdir()
        (self.previous / 'extra/pyproject.toml').write_text('[project]\nname="owned-package"\nversion="0.24.0"\n')
        with self.assertRaisesRegex(ValueError, 'Duplicate package identity'):
            module.release_required(self.current, self.previous, 'example/component', '0.24')
