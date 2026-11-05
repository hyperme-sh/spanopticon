import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('component_release', Path(__file__).resolve().parents[1] / 'scripts/component_release.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class ComponentReleaseTest(unittest.TestCase):
    def setUp(self):
        stable = patch.object(module, 'verify_accepted_record', return_value='0.24.0')
        self.stable = stable.start(); self.addCleanup(stable.stop)
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
        self.stable.assert_called_with(self.previous, 'example/component', required=True)
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

class InitialReleaseTest(unittest.TestCase):
    bundle = ComponentReleaseTest.bundle
    def initial(self, fetch=None, requested='v0.24.0'):
        return module.admit_initial(self.current, 'example/component', '0.24', requested,
                                    fetch=fetch or (lambda path: []))

    def setUp(self):
        ComponentReleaseTest.setUp(self)
        self.bundle(self.current, 'candidate', '0.24.0')

    def test_empty_release_inventory_admits_declaration(self):
        self.assertEqual(self.initial(), '0.24.0')

    def test_test_snapshots_do_not_reserve_stable_version(self):
        self.assertEqual(self.initial(lambda path: [{'prerelease': True}]), '0.24.0')

    def test_stable_release_on_later_page_blocks_bootstrap(self):
        def fetch(path):
            return [{'prerelease': True}] * 100 if path.endswith('page=1') else [{'prerelease': False}]
        with self.assertRaisesRegex(ValueError, 'baseline is required'):
            self.initial(fetch)

    def test_missing_classification_and_unbounded_inventory_fail_closed(self):
        for reply in ([{}], {}, [{'prerelease': True}] * 100):
            with self.subTest(reply=str(reply)[:30]), self.assertRaises(ValueError):
                self.initial(lambda path: reply)

    def test_existing_accepted_record_cannot_be_bypassed(self):
        (self.current / 'releases/accepted').mkdir()
        with self.assertRaisesRegex(ValueError, 'accepted record'):
            self.initial()

    def test_initial_tag_and_package_version_are_bound(self):
        with self.assertRaisesRegex(ValueError, 'tag version'):
            self.initial(requested='v0.24.1')
        (self.current / 'pyproject.toml').write_text('[project]\nversion="0.24.1"\n')
        with self.assertRaisesRegex(ValueError, 'package version'):
            self.initial()

    def test_api_error_cannot_establish_first_release(self):
        with self.assertRaises(OSError):
            self.initial(lambda path: (_ for _ in ()).throw(OSError('offline')))
