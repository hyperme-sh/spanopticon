import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from urllib.error import HTTPError

from release.accepted import github_asset, verify_accepted_record


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
        return verify_accepted_record(self.root, 'example/component', required=True, fetch=self.fetch,
                                      fetch_asset=lambda repository, asset_id: self.attachment)

    def container(self, document=None):
        artifact = {'kind': 'container', 'uri': 'ghcr.io/example/component@sha256:'+'e'*64, 'sha256': 'e'*64}
        self.bundle['components']['component']['artifact'] = artifact
        document = document or {'schema_version': 1, 'repository': 'example/component',
            'revision': 'b'*40, 'version': '0.24.0', 'artifact': artifact}
        self.attachment = json.dumps(document).encode()
        digest = 'sha256:' + hashlib.sha256(self.attachment).hexdigest()
        self.provenance['assets'] = {'container-provenance.json': digest}
        self.provenance['container_provenance_asset'] = 'container-provenance.json'
        self.release['assets'] = [{'id': 9, 'name': 'container-provenance.json', 'digest': digest}]
        return document

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

    def test_container_manifest_digest_is_bound_through_published_provenance(self):
        self.container()
        self.assertNotEqual(self.provenance['assets']['container-provenance.json'], 'sha256:'+'e'*64)
        self.assertEqual(self.verify(), '0.24.0')

    def test_container_provenance_must_bind_the_owner_source_version_and_image(self):
        original = self.container()
        for field, value in [('repository', 'other/component'), ('revision', 'c'*40),
                             ('version', '0.24.1'), ('artifact', {'kind': 'container'})]:
            with self.subTest(field=field):
                invalid = {**original, field: value}
                self.container(invalid)
                with self.assertRaisesRegex(ValueError, 'does not bind'): self.verify()

    def test_container_provenance_download_is_checked_against_live_asset_digest(self):
        self.container()
        self.attachment += b' '
        with self.assertRaisesRegex(ValueError, 'bytes differ'): self.verify()

    def test_container_without_a_published_provenance_attachment_is_rejected(self):
        self.container()
        self.provenance.pop('container_provenance_asset')
        with self.assertRaisesRegex(ValueError, 'attachment required'): self.verify()

    def test_asset_cdn_redirect_never_receives_the_api_credential(self):
        opener = Mock()
        redirect = HTTPError('https://api.github.com/owned', 302, 'redirect',
            {'Location': 'https://release-assets.githubusercontent.com/owned?signature=fixture'}, io.BytesIO(b''))
        opener.open.side_effect = [redirect, io.BytesIO(b'provenance')]
        with patch('urllib.request.build_opener', return_value=opener), patch.dict(os.environ, {'GH_TOKEN': 'owned-fixture-token'}):
            self.assertEqual(github_asset('example/component', 9), b'provenance')
        first, second = (call.args[0] for call in opener.open.call_args_list)
        self.assertEqual(first.get_header('Authorization'), 'Bearer owned-fixture-token')
        self.assertFalse(any(key.lower() == 'authorization' for key, _ in second.header_items()))

    def test_asset_redirects_to_other_origins_are_rejected(self):
        for location in ['https://attacker.invalid/file', 'http://release-assets.githubusercontent.com/file',
                         'https://user@release-assets.githubusercontent.com/file',
                         'https://release-assets.githubusercontent.com:444/file']:
            with self.subTest(location=location):
                opener = Mock()
                opener.open.side_effect = HTTPError('https://api.github.com/owned', 302, 'redirect',
                    {'Location': location}, io.BytesIO(b''))
                with patch('urllib.request.build_opener', return_value=opener), self.assertRaises(ValueError):
                    github_asset('example/component', 9)
                self.assertEqual(opener.open.call_count, 1)

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
