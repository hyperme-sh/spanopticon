import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from release.preparation import prepare


class PreparationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Unit fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.source / 'requirements.lock').write_text('package==1.0.0\n')
        self.git('add', 'requirements.lock')
        self.git('commit', '-qm', 'Synthetic locked source')
        self.output = self.root / 'output'
        self.output.mkdir()
        artifact = b'synthetic package bytes; no installed-consumer claim'
        contract = b'{"api":"synthetic"}'
        (self.output / 'package.bin').write_bytes(artifact)
        (self.output / 'contract.json').write_bytes(contract)
        self.candidate = self.output / 'release.json'
        self.candidate.write_text(json.dumps({'schema_version': 1, 'line': '1.0', 'components': {
            'package': {'version': '1.0.0', 'contract': 'contract.json',
                        'contract_sha256': hashlib.sha256(contract).hexdigest(),
                        'artifact': {'kind': 'package', 'uri': 'https://example.invalid/package',
                                     'sha256': hashlib.sha256(artifact).hexdigest()}}}}))
        self.record = {'schema_version': 1, 'source_revision': self.git('rev-parse', 'HEAD'),
                       'locks': {'requirements.lock': hashlib.sha256((self.source / 'requirements.lock').read_bytes()).hexdigest()},
                       'artifacts': {'package': 'package.bin'}}
        self.provenance = self.output / 'preparation.json'

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.source), *args], stderr=subprocess.DEVNULL).decode().strip()

    def check(self):
        self.provenance.write_text(json.dumps(self.record))
        return prepare(self.candidate, self.provenance, self.source)

    def test_no_stable_baseline_is_required_or_publication_authorized(self):
        result = self.check()
        self.assertFalse(result['publication_authorized'])
        self.assertEqual(result['components'], ['package'])
        self.assertEqual(result['purpose'], 'preparation-inputs-only')

    def test_missing_source_or_lock_provenance_is_rejected(self):
        for field in ('source_revision', 'locks', 'artifacts'):
            with self.subTest(field=field):
                value = self.record.pop(field)
                with self.assertRaises(ValueError): self.check()
                self.record[field] = value

    def test_wrong_revision_is_rejected(self):
        self.record['source_revision'] = '0' * 40
        with self.assertRaisesRegex(ValueError, 'revision differs'): self.check()

    def test_dirty_source_is_rejected(self):
        (self.source / 'requirements.lock').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'tracked changes'): self.check()

    def test_wrong_lock_digest_is_rejected(self):
        self.record['locks']['requirements.lock'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'lock differs'): self.check()

    def test_untracked_lock_cannot_supply_provenance(self):
        (self.source / 'untracked.lock').write_text('untracked')
        self.record['locks'] = {'untracked.lock': hashlib.sha256(b'untracked').hexdigest()}
        with self.assertRaises(subprocess.CalledProcessError): self.check()

    def test_tampered_artifact_or_contract_is_rejected(self):
        for name in ('package.bin', 'contract.json'):
            path = self.output / name
            original = path.read_bytes()
            path.write_bytes(b'tampered')
            with self.subTest(name=name), self.assertRaises(ValueError): self.check()
            path.write_bytes(original)

    def test_extra_or_missing_components_are_rejected(self):
        self.record['artifacts']['unexpected'] = 'package.bin'
        with self.assertRaisesRegex(ValueError, 'inventory'): self.check()
        self.record['artifacts'] = {}
        with self.assertRaisesRegex(ValueError, 'inventory'): self.check()

    def test_paths_cannot_escape_input_roots(self):
        for value in ('../source/requirements.lock', '/etc/passwd'):
            self.record['artifacts']['package'] = value
            with self.subTest(value=value), self.assertRaises(ValueError): self.check()
        (self.output / 'link').symlink_to(self.source / 'requirements.lock')
        self.record['artifacts']['package'] = 'link'
        with self.assertRaises(ValueError): self.check()

    def test_assume_unchanged_lock_cannot_hide_modified_bytes(self):
        self.git('update-index', '--assume-unchanged', 'requirements.lock')
        (self.source / 'requirements.lock').write_text('changed')
        self.record['locks']['requirements.lock'] = hashlib.sha256(b'changed').hexdigest()
        with self.assertRaisesRegex(ValueError, 'index flags'): self.check()

    def test_boolean_schema_is_not_a_version(self):
        self.record['schema_version'] = True
        with self.assertRaisesRegex(ValueError, 'schema|provenance'): self.check()

    def test_index_flags_cannot_hide_non_lock_source_changes(self):
        for flag in ('--assume-unchanged', '--skip-worktree'):
            with self.subTest(flag=flag):
                path = self.source / 'app.py'
                path.write_text('original')
                self.git('add', 'app.py')
                self.git('commit', '-qm', 'Add source')
                self.record['source_revision'] = self.git('rev-parse', 'HEAD')
                self.git('update-index', flag, 'app.py')
                path.write_text('changed')
                with self.assertRaisesRegex(ValueError, 'index flags'): self.check()
                self.git('update-index', flag.replace('--', '--no-'), 'app.py')
                path.write_text('original')
                self.git('rm', 'app.py')
                self.git('commit', '-qm', 'Remove fixture source')
