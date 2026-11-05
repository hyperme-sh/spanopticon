import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from release.accepted import verify_accepted_record


class AcceptedBaselineTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.folder = self.root / 'releases/accepted'
        self.folder.mkdir(parents=True)
        raw = b'{"public_api":{"read":"str"}}'
        (self.folder / 'contract.json').write_bytes(raw)
        self.bundle = {'schema_version': 1, 'line': '0.24', 'components': {'component': {
            'version': '0.24.0', 'contract': 'contract.json',
            'contract_sha256': hashlib.sha256(raw).hexdigest(),
            'artifact': {'kind': 'package', 'uri': 'urn:sha256:' + 'a'*64, 'sha256': 'a'*64}}}}
        self.provenance = {'repository': 'example/component', 'revision': 'b'*40,
            'release_id': 7, 'tag': 'v0.24.0', 'assets': {'component.whl': 'sha256:'+'a'*64}}
        self.release = {'id': 7, 'tag_name': 'v0.24.0', 'prerelease': False,
            'draft': False, 'immutable': True,
            'assets': [{'name': 'component.whl', 'digest': 'sha256:'+'a'*64}]}
        self.reference = {'ref': 'refs/tags/v0.24.0', 'object': {'type': 'commit', 'sha': 'b'*40}}
        self.calls = []
        self.extra = {}

    def fetch(self, path):
        self.calls.append(path)
        values = {'repos/example/component/releases/7': self.release,
                  'repos/example/component/git/ref/tags/v0.24.0': self.reference, **self.extra}
        self.assertIn(path, values)
        return copy.deepcopy(values[path])

    def verify(self):
        (self.folder / 'release.json').write_text(json.dumps(self.bundle))
        (self.folder / 'provenance.json').write_text(json.dumps(self.provenance))
        return verify_accepted_record(self.root, 'example/component', required=True, fetch=self.fetch)

    def test_real_immutable_stable_release_binds_source_and_asset(self):
        self.assertEqual(self.verify(), '0.24.0')
        self.assertEqual(len(self.calls), 2)

    def test_test_tag_cannot_reserve_a_stable_version(self):
        self.provenance['tag'] = 'test-' + 'b'*40
        with self.assertRaisesRegex(ValueError, 'test snapshots'):
            self.verify()
        self.assertEqual(self.calls, [])

    def test_forged_stable_label_does_not_override_live_prerelease(self):
        self.release['prerelease'] = True
        self.provenance['prerelease'] = False
        self.provenance['immutable'] = True
        with self.assertRaisesRegex(ValueError, 'prerelease or draft'):
            self.verify()

    def test_missing_or_false_live_stable_properties_fail_closed(self):
        for name, invalid in [('prerelease', True), ('prerelease', None), ('prerelease', 0),
                              ('draft', True), ('draft', None), ('immutable', False), ('immutable', None)]:
            with self.subTest(name=name, invalid=invalid):
                previous = self.release[name]
                self.release[name] = invalid
                with self.assertRaises(ValueError): self.verify()
                self.release[name] = previous

    def test_actual_release_id_and_tag_must_match(self):
        for name, invalid in [('id', 8), ('tag_name', 'v0.24.1')]:
            with self.subTest(name=name):
                previous = self.release[name]
                self.release[name] = invalid
                with self.assertRaisesRegex(ValueError, 'identity mismatch'): self.verify()
                self.release[name] = previous

    def test_unrelated_or_moved_tag_cannot_supply_provenance(self):
        self.reference['object']['sha'] = 'c'*40
        with self.assertRaisesRegex(ValueError, 'source differs'): self.verify()
        self.reference['ref'] = 'refs/tags/other'
        with self.assertRaisesRegex(ValueError, 'reference mismatch'): self.verify()

    def test_annotated_stable_tag_is_peeled_and_bound(self):
        self.reference['object'] = {'type': 'tag', 'sha': 'c'*40}
        self.extra['repos/example/component/git/tags/'+'c'*40] = {'object': {'type': 'commit', 'sha': 'b'*40}}
        self.assertEqual(self.verify(), '0.24.0')

    def test_cyclic_or_wrong_type_tag_fails_closed(self):
        self.reference['object'] = {'type': 'tag', 'sha': 'c'*40}
        self.extra['repos/example/component/git/tags/'+'c'*40] = {'object': {'type': 'tag', 'sha': 'c'*40}}
        with self.assertRaisesRegex(ValueError, 'Invalid stable tag'): self.verify()
        self.reference['object']['type'] = 'tree'
        with self.assertRaisesRegex(ValueError, 'Unsupported stable tag'): self.verify()

    def test_stable_asset_digest_is_authoritative(self):
        for assets in [[], [{'name': 'component.whl'}],
                       [{'name': 'component.whl', 'digest': 'sha256:'+'d'*64}],
                       self.release['assets'] * 2]:
            with self.subTest(assets=assets):
                self.release['assets'] = assets
                with self.assertRaises(ValueError): self.verify()

    def test_component_cannot_point_at_an_unrelated_stable_asset(self):
        self.bundle['components']['component']['artifact']['sha256'] = 'e'*64
        with self.assertRaisesRegex(ValueError, 'absent from stable asset'): self.verify()

    def test_repository_and_numeric_identity_cannot_be_spoofed(self):
        for value in [True, '7', 0, -1]:
            with self.subTest(value=value):
                self.provenance['release_id'] = value
                with self.assertRaisesRegex(ValueError, 'release ID'): self.verify()
        self.provenance['release_id'] = 7
        self.provenance['repository'] = 'other/component'
        with self.assertRaisesRegex(ValueError, 'repository mismatch'): self.verify()

    def test_no_stable_record_is_distinct_from_acceptance(self):
        self.assertIsNone(verify_accepted_record(self.root, 'example/component', fetch=self.fetch))
        with self.assertRaisesRegex(ValueError, 'No accepted stable release'):
            verify_accepted_record(self.root, 'example/component', required=True, fetch=self.fetch)

    def test_partial_records_and_external_symlinks_are_rejected(self):
        self.verify()
        (self.folder / 'provenance.json').unlink()
        with self.assertRaisesRegex(ValueError, 'manifest and provenance'):
            verify_accepted_record(self.root, 'example/component', fetch=self.fetch)
        outside = self.root.parent / (self.root.name + '-external-provenance.json')
        outside.write_text(json.dumps(self.provenance))
        self.addCleanup(outside.unlink)
        (self.folder / 'provenance.json').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'escapes checkout'):
            verify_accepted_record(self.root, 'example/component', fetch=self.fetch)
