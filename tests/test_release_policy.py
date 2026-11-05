import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('policy', Path(__file__).resolve().parents[1] / 'release/policy.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def api():
    return {'openapi': '3.1.0', 'info': {'title': 'Example', 'version': '1.4.0'},
            'paths': {'/items/{id}': {'get': {'responses': {'200': {'description': 'ok'}}}}},
            'components': {'schemas': {'Item': {'type': 'object', 'properties': {'id': {'type': 'string'}}}}}}


class ReleasePolicyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def bundle(self, folder, versions=None, document=None, line='1.4', digest='a' * 64):
        versions = versions or {'api': '1.4.0', 'worker': '1.4.0'}
        root = self.root / folder
        root.mkdir()
        components = {}
        for name, ver in versions.items():
            doc = copy.deepcopy(document if document is not None else api())
            doc['info']['version'] = ver
            raw = json.dumps(doc).encode()
            (root / (name + '.json')).write_bytes(raw)
            components[name] = {'version': ver, 'contract': name + '.json',
                'contract_sha256': hashlib.sha256(raw).hexdigest(),
                'artifact': {'kind': 'container', 'uri': 'registry.example.org/' + name + '@sha256:' + digest,
                             'sha256': digest}}
        manifest = root / 'release.json'
        manifest.write_text(json.dumps({'schema_version': 1, 'line': line, 'components': components}))
        return manifest

    def rewrite(self, path, edit):
        data = json.loads(path.read_text())
        edit(data)
        path.write_text(json.dumps(data))

    def test_independent_patch_numbers_accept_unchanged_api(self):
        old = self.bundle('old')
        new = self.bundle('new', {'api': '1.4.1', 'worker': '1.4.9'}, digest='b' * 64)
        self.assertEqual(policy.validate(old, new)['line'], '1.4')

    def test_patch_rejects_method_schema_auth_and_response_changes(self):
        mutations = [
            lambda d: d['paths']['/items/{id}'].clear(),
            lambda d: d['components']['schemas']['Item']['properties']['id'].update(type='integer'),
            lambda d: d.update(security=[{'bearer': []}]),
            lambda d: d['paths']['/items/{id}']['get']['responses'].update({'403': {'description': 'denied'}}),
            lambda d: d['paths'].update({'/other': {'get': {'responses': {'200': {'description': 'ok'}}}}}),
        ]
        old = self.bundle('old')
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                doc = api(); mutate(doc)
                new = self.bundle('new' + str(index), {'api': '1.4.1', 'worker': '1.4.1'}, doc)
                with self.assertRaisesRegex(policy.PolicyError, 'patch changed'):
                    policy.validate(old, new)

    def test_mixed_minor_versions_rejected(self):
        old = self.bundle('old')
        new = self.bundle('new', {'api': '1.5.0', 'worker': '1.4.1'}, line='1.5')
        with self.assertRaisesRegex(policy.PolicyError, 'coordinated line'):
            policy.validate(old, new)

    def test_coordinated_minor_accepts_new_disjoint_path(self):
        old = self.bundle('old'); doc = api()
        doc['paths']['/health'] = {'get': {'responses': {'200': {'description': 'ok'}}}}
        new = self.bundle('new', {'api': '1.5.0', 'worker': '1.5.3'}, doc, line='1.5')
        policy.validate(old, new)

    def test_minor_rejects_schema_break_and_shadowing_path(self):
        old = self.bundle('old')
        for i, doc in enumerate([api(), api()]):
            if i == 0: doc['components']['schemas']['Item']['properties']['id']['type'] = 'integer'
            else: doc['paths']['/items/me'] = {'get': {'responses': {'200': {'description': 'ok'}}}}
            new = self.bundle('new' + str(i), {'api': '1.5.0', 'worker': '1.5.0'}, doc, line='1.5')
            with self.assertRaisesRegex(policy.PolicyError, 'minor compatibility'):
                policy.validate(old, new)

    def test_major_accepts_breaking_contract(self):
        old = self.bundle('old'); doc = api(); doc['paths'] = {}
        new = self.bundle('new', {'api': '2.0.0'}, doc, line='2.0')
        policy.validate(old, new)

    def test_same_version_artifact_cannot_be_replaced(self):
        old, new = self.bundle('old'), self.bundle('new', digest='b' * 64)
        with self.assertRaisesRegex(policy.PolicyError, 'immutable'): policy.validate(old, new)

    def test_rollback_and_patch_membership_change_rejected(self):
        old = self.bundle('old', {'api': '1.4.5', 'worker': '1.4.1'})
        new = self.bundle('new')
        with self.assertRaisesRegex(policy.PolicyError, 'rollback'): policy.validate(old, new)
        removed = self.bundle('removed', {'api': '1.4.6'})
        with self.assertRaisesRegex(policy.PolicyError, 'membership'): policy.validate(old, removed)

    def test_digest_tampering_rejected(self):
        path = self.bundle('new'); (path.parent / 'api.json').write_text('{}')
        with self.assertRaisesRegex(policy.PolicyError, 'digest mismatch'): policy.load_bundle(path)

    def test_missing_baseline_is_an_error(self):
        with self.assertRaises(FileNotFoundError): policy.validate(self.root / 'absent', self.bundle('new'))

    def test_contract_path_and_symlink_cannot_escape_bundle(self):
        for name, value in [('parent', '../outside.json'), ('absolute', '/tmp/outside.json'), ('symlink', 'link.json')]:
            with self.subTest(name=name):
                path = self.bundle(name)
                outside = self.root / 'outside.json'; outside.write_text('{}')
                (path.parent / 'link.json').symlink_to(outside)
                self.rewrite(path, lambda d: d['components']['api'].update(contract=value))
                with self.assertRaises(policy.PolicyError): policy.load_bundle(path)

    def test_exported_api_version_must_match_component(self):
        path = self.bundle('new')
        self.rewrite(path, lambda d: d['components']['api'].update(version='1.4.1'))
        with self.assertRaisesRegex(policy.PolicyError, 'exported API version'): policy.load_bundle(path)

    def test_duplicate_json_keys_and_nonfinite_values_rejected(self):
        for i, raw in enumerate(['{"x":1,"x":2}', '{"x":NaN}']):
            path = self.root / str(i); path.write_text(raw)
            with self.assertRaises(policy.PolicyError): policy.json_document(path)

    def test_mutable_image_and_invalid_semver_rejected(self):
        for i, val in enumerate(['1.4', '01.4.0', '1.4.0-rc.1']):
            with self.subTest(version=val), self.assertRaises(policy.PolicyError): policy.version(val)
        path = self.bundle('new')
        self.rewrite(path, lambda d: d['components']['api']['artifact'].update(uri='example:latest'))
        with self.assertRaisesRegex(policy.PolicyError, 'digest/URI'): policy.load_bundle(path)

    def test_render_uses_validated_digests_and_refuses_incompatible_bundle(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
        from render_release import render
        from test_generator import ASSURANCE
        old = self.bundle('old')
        new = self.bundle('new', {'api': '1.4.1', 'worker': '1.4.3'}, digest='b' * 64)
        output = render(old, new, self.root / 'rendered', ASSURANCE)
        policy.load_bundle(output / 'release.json')
        compose = json.loads((output / 'api/compose.yaml').read_text())
        self.assertEqual(compose['services']['api']['image'].split('@sha256:')[1], 'b' * 64)
        with self.assertRaises(FileExistsError): render(old, new, output, ASSURANCE)
        doc = api(); doc['paths'] = {}
        bad = self.bundle('bad', {'api': '1.4.1', 'worker': '1.4.1'}, doc)
        # The CLI rejects an incompatible bundle before creating output.
        with self.assertRaises(ValueError): render(old, bad, self.root / 'bad-output', ASSURANCE)
        self.assertFalse((self.root / 'bad-output').exists())


    def test_boolean_and_numeric_schema_values_are_not_equivalent(self):
        old_doc = api(); old_doc['components']['schemas']['Item']['enum'] = [1]
        new_doc = api(); new_doc['components']['schemas']['Item']['enum'] = [True]
        old = self.bundle('old', document=old_doc)
        new = self.bundle('new', {'api': '1.4.1', 'worker': '1.4.1'}, new_doc)
        with self.assertRaisesRegex(policy.PolicyError, 'patch changed'): policy.validate(old, new)


    def test_minor_addition_cannot_change_existing_reference_target(self):
        old_doc = api()
        old_doc['components']['schemas'] = {}
        old_doc['paths']['/items/{id}']['get']['responses']['200']['content'] = {
            'application/json': {'schema': {'$ref': '#/components/schemas'}}}
        new_doc = copy.deepcopy(old_doc)
        new_doc['components']['schemas']['not'] = {}
        old = self.bundle('old', document=old_doc)
        new = self.bundle('new', {'api': '1.5.0', 'worker': '1.5.0'}, new_doc, line='1.5')
        with self.assertRaisesRegex(policy.PolicyError, 'minor compatibility'): policy.validate(old, new)

    def test_minor_unknown_reference_scopes_fail_closed(self):
        for index, keyword in enumerate(['$dynamicRef', '$id', '$anchor']):
            old_doc = api(); old_doc['components']['schemas']['Item'][keyword] = '#example'
            new_doc = copy.deepcopy(old_doc)
            new_doc['paths']['/health'] = {'get': {'responses': {'200': {'description': 'ok'}}}}
            old = self.bundle('old' + str(index), document=old_doc)
            new = self.bundle('new' + str(index), {'api': '1.5.0', 'worker': '1.5.0'}, new_doc, line='1.5')
            with self.subTest(keyword=keyword), self.assertRaisesRegex(policy.PolicyError, 'minor compatibility'):
                policy.validate(old, new)


    def test_minor_accepts_new_schema_used_only_by_new_operation(self):
        old_doc = api()
        old_doc['paths']['/items/{id}']['get']['responses']['200']['content'] = {
            'application/json': {'schema': {'$ref': '#/components/schemas/Item'}}}
        new_doc = copy.deepcopy(old_doc)
        new_doc['components']['schemas']['Health'] = {'type': 'boolean'}
        new_doc['paths']['/health'] = {'get': {'responses': {'200': {
            'description': 'ok', 'content': {'application/json': {
                'schema': {'$ref': '#/components/schemas/Health'}}}}}}}
        old = self.bundle('old', document=old_doc)
        new = self.bundle('new', {'api': '1.5.0', 'worker': '1.5.0'}, new_doc, line='1.5')
        policy.validate(old, new)
