import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
import zipfile

from release.installer import snapshot, compatible, verify_direct_urls


class InstallerTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.wheel = self.root / 'pkg.whl'
        with zipfile.ZipFile(self.wheel, 'w') as archive:
            archive.writestr('pkg-1.0.0.dist-info/METADATA',
                'Name: pkg\nVersion: 1.0.0\nRequires-Python: >=3.11\nRequires-Dist: dep>=1\nProvides-Extra: test\n\n')
        self.metadata = snapshot(self.wheel)

    def test_each_installer_surface_refuses_change(self):
        for key, changed in [('requires_python', '>=3.12'), ('requires_dist', ['dep>=2']),
                             ('provides_extra', ['different']), ('entry_points_sha256', '0' * 64)]:
            value = dict(self.metadata, **{key: changed})
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'Incompatible'):
                compatible(self.metadata, value)

    def test_patch_package_version_is_independent(self):
        compatible(self.metadata, dict(self.metadata, version='1.0.9'))

    def test_incomplete_old_metadata_cannot_claim_compatibility(self):
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            compatible({}, self.metadata)

    def test_duplicate_distribution_rejected(self):
        with zipfile.ZipFile(self.wheel, 'a') as archive:
            archive.writestr('other-1.0.dist-info/METADATA', 'Name: other\n')
        with self.assertRaisesRegex(ValueError, 'one wheel'):
            snapshot(self.wheel)

    def direct(self):
        digest = hashlib.sha256(self.wheel.read_bytes()).hexdigest()
        url = 'https://example.invalid/pkg.whl#sha256=' + digest
        lock = {'pkg': {'url': url, 'sha256': digest, 'path': self.wheel.name}}
        installed = {'pkg': {'url': url.split('#')[0], 'archive_info': {'hashes': {'sha256': digest}}}}
        return url, lock, installed

    def test_direct_url_tampering_and_missing_provenance_refused(self):
        url, lock, installed = self.direct()
        for bad_lock, bad_installed in [({}, installed), (lock, {}),
                (lock, {'pkg': {'url': installed['pkg']['url'], 'archive_info': {}}})]:
            with self.subTest(lock=bad_lock), self.assertRaises(ValueError):
                verify_direct_urls(['pkg @ ' + url], bad_lock, bad_installed, self.root)
        self.wheel.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            verify_direct_urls(['pkg @ ' + url], lock, installed, self.root)

    def test_non_https_credentials_and_unlocked_fragments_rejected(self):
        url, lock, installed = self.direct()
        for invalid in (url.replace('https:', 'http:'), url.replace('example.invalid', 'secret@example.invalid'),
                        url.split('#')[0], 'git+https://example.invalid/pkg.git@main'):
            bad = copy.deepcopy(lock)
            bad['pkg']['url'] = invalid
            with self.subTest(url=invalid), self.assertRaises(ValueError):
                verify_direct_urls(['pkg @ ' + invalid], bad, installed, self.root)

    def test_inactive_and_mutually_exclusive_urls_use_selected_environment(self):
        import sys
        url, lock, installed = self.direct()
        other = 'https://other.invalid/pkg.whl'
        active = f'pkg @ {url} ; sys_platform == "{sys.platform}"'
        inactive = f'pkg @ {other} ; sys_platform != "{sys.platform}"'
        verify_direct_urls([inactive], {}, {}, self.root)
        verify_direct_urls([active, inactive], lock, installed, self.root)

    def test_optional_url_is_required_only_for_selected_extra(self):
        url, lock, installed = self.direct()
        requirement = 'pkg @ ' + url + ' ; extra == "optional"'
        verify_direct_urls([requirement], {}, {}, self.root)
        verify_direct_urls([requirement], lock, installed, self.root, extras=['optional'])
        with self.assertRaises(ValueError):
            verify_direct_urls([requirement], {}, {}, self.root, extras=['optional'])
