"""Additive package files must not become an escape hatch for changed bytes."""
import copy
import unittest
from release.policy import compatible, additive_python_modules


def manifest():
    return {'schema_version': 1, 'coverage': 'complete packaged runtime',
            'packaged_file_sha256': {'identity/__init__.py': 'a'*64,
                                    'identity/security.py': 'b'*64}}


class PythonModules(unittest.TestCase):
    def test_addition_is_compatible_without_modifying_prior_runtime(self):
        old = manifest(); new = copy.deepcopy(old)
        new['packaged_file_sha256']['identity/account_lifecycle.py'] = 'c'*64
        self.assertTrue(compatible(old, new))
        self.assertFalse(compatible(new, old))

    def test_existing_bytes_and_contract_metadata_are_immutable(self):
        old = manifest()
        for target in ('identity/__init__.py', 'identity/security.py', 'coverage', 'schema_version'):
            new = copy.deepcopy(old)
            new['packaged_file_sha256']['identity/new.py'] = 'c'*64
            if target in new['packaged_file_sha256']:
                new['packaged_file_sha256'][target] = 'd'*64
            else:
                new[target] = 'changed'
            with self.subTest(target=target): self.assertFalse(compatible(old, new))

    def test_unsafe_new_files_and_package_shadowing_are_rejected(self):
        old = manifest()
        old['packaged_file_sha256']['identity/nested/__init__.py'] = 'd'*64
        for name in ('identity/__main__.py', 'identity/nested.py', 'identity/Security.py',
                     'identity/new/__init__.py', 'other/new.py', 'identity/new.so',
                     'identity/../new.py', '/identity/new.py', 'identity/new.pth',
                     'identity/new-module.py', 'identity/evil\\name.py'):
            new = copy.deepcopy(old); new['packaged_file_sha256'][name] = 'c'*64
            with self.subTest(name=name): self.assertFalse(compatible(old, new))

    def test_malformed_manifests_and_unknown_fields_are_rejected(self):
        old = manifest()
        for digest in ('bad', None, 1):
            new = copy.deepcopy(old); new['packaged_file_sha256']['identity/new.py'] = digest
            self.assertFalse(additive_python_modules(old, new))
        new = copy.deepcopy(old); new['extra'] = 'unrecognized'
        self.assertFalse(compatible(old, new))
        self.assertFalse(additive_python_modules({}, new))

    def test_real_release_bundle_requires_minor_version_for_additions(self):
        import hashlib
        import json
        from pathlib import Path
        import tempfile
        from release.policy import validate, PolicyError
        with tempfile.TemporaryDirectory() as temp:
            def bundle(name, version, document):
                root = Path(temp) / name; root.mkdir()
                raw = json.dumps(document).encode(); (root/'contract.json').write_bytes(raw)
                data = {'schema_version': 1, 'line': '.'.join(version.split('.')[:2]),
                        'components': {'identity': {'version': version, 'contract': 'contract.json',
                            'contract_sha256': hashlib.sha256(raw).hexdigest(),
                            'artifact': {'kind': 'package', 'uri': 'urn:sha256:'+'a'*64, 'sha256': 'a'*64}}}}
                path = root/'release.json'; path.write_text(json.dumps(data)); return path
            old = manifest(); new = copy.deepcopy(old)
            new['packaged_file_sha256']['identity/account_lifecycle.py'] = 'c'*64
            before = bundle('old', '0.24.0', old)
            with self.assertRaisesRegex(PolicyError, 'patch changed'):
                validate(before, bundle('patch', '0.24.1', new))
            self.assertEqual(validate(before, bundle('minor', '0.25.0', new))['line'], '0.25')
