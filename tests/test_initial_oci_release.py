import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from component_release import admit_initial
import initial_oci_release as policy


class InitialOciReleaseTest(unittest.TestCase):
    def setUp(self):
        checkout = tempfile.TemporaryDirectory()
        self.addCleanup(checkout.cleanup)
        self.root = Path(checkout.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        self.write('pyproject.toml', '[project]\nversion="0.1.0"\n')
        self.write('release-policy.json', {
            'schema_version': 1, 'repository': policy.OWNER, 'component': 'hypermesh-gateway',
            'line': '0.1', 'version_files': ['pyproject.toml'],
        })
        self.contract = '{"public_api":{"read":"str"}}\n'
        self.write('releases/prepared/contract.json', self.contract)
        self.source = self.commit()
        self.digest = 'a' * 64
        self.write_candidate(self.contract, policy.IMAGE_REPOSITORY, self.digest)
        self.write('releases/candidate/oci-source.json', {
            'source_revision': self.source, 'run_id': 42, 'run_attempt': 2,
        })
        self.revision = self.commit()
        self.releases = []
        self.build = {
            'id': 42, 'run_attempt': 2, 'repository': {'full_name': policy.OWNER},
            'head_repository': {'full_name': policy.OWNER}, 'path': policy.BUILD_WORKFLOW,
            'head_sha': self.source, 'head_branch': 'main', 'event': 'push',
            'status': 'completed', 'conclusion': 'success',
        }
        self.jobs = {'total_count': 3, 'jobs': [
            {'name': name, 'head_sha': self.source, 'run_id': 42, 'run_attempt': 2,
             'status': 'completed', 'conclusion': 'success'}
            for name in ('package', 'publish', f'Qualified OCI sha256:{self.digest}')
        ]}

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'Fixture')
        return self.git('rev-parse', 'HEAD')

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value))

    def write_candidate(self, contract, image, digest):
        self.write('releases/candidate/contract.json', contract)
        self.write('releases/candidate/release.json', {
            'schema_version': 1, 'line': '0.1', 'components': {'hypermesh-gateway': {
                'version': '0.1.0', 'contract': 'contract.json',
                'contract_sha256': hashlib.sha256(contract.encode()).hexdigest(),
                'artifact': {'kind': 'container', 'uri': f'{image}@sha256:{digest}', 'sha256': digest},
            }},
        })

    def fetch(self, path, *, document_type=dict):
        root = f'repos/{policy.OWNER}'
        return {
            f'{root}/releases?per_page=100&page=1': self.releases,
            f'{root}/actions/runs/42/attempts/2': self.build,
            f'{root}/actions/runs/42/attempts/2/jobs?per_page=100': self.jobs,
        }[path]

    def admit(self, *, ref='refs/heads/main', repository=policy.OWNER):
        return policy.admit(self.root, repository, '0.1', 'v0.1.0', self.revision, ref, fetch=self.fetch)

    def test_qualified_image_is_admitted_from_main_and_its_exact_tag(self):
        expected = ('v0.1.0', f'{policy.IMAGE_REPOSITORY}@sha256:{self.digest}', self.source)
        self.assertEqual(self.admit(), expected)
        self.git('tag', '-a', '-m', 'fixture release', 'v0.1.0')
        self.assertEqual(self.admit(ref='refs/tags/v0.1.0'), expected)
        with self.assertRaisesRegex(ValueError, 'Stable tag already exists'):
            self.admit()

    def test_non_owner_and_package_entry_point_refuse_oci(self):
        with self.assertRaisesRegex(ValueError, 'Gateway owner only'):
            self.admit(repository='example/gateway')
        with self.assertRaisesRegex(ValueError, 'package distribution'):
            admit_initial(self.root, policy.OWNER, '0.1', 'v0.1.0')

    def test_prior_stable_history_blocks_oci_bootstrap(self):
        self.releases = [{'prerelease': False}]
        with self.assertRaisesRegex(ValueError, 'baseline is required'):
            self.admit()
        self.releases = [{'prerelease': True}]
        self.git('tag', 'v0.0.1', self.source)
        with self.assertRaisesRegex(ValueError, 'Stable tag already exists'):
            self.admit()

    def test_moved_tag_and_changed_source_are_refused(self):
        self.git('tag', 'v0.1.0', self.source)
        with self.assertRaisesRegex(ValueError, 'different revision'):
            self.admit(ref='refs/tags/v0.1.0')
        self.git('tag', '-d', 'v0.1.0')
        self.write('gateway.py', 'print("different runtime")\n')
        self.revision = self.commit()
        with self.assertRaisesRegex(ValueError, 'outside release metadata'):
            self.admit()

    def test_wrong_contract_and_image_are_refused(self):
        self.write_candidate('{"different":true}\n', policy.IMAGE_REPOSITORY, self.digest)
        self.revision = self.commit()
        with self.assertRaisesRegex(ValueError, 'differs from the qualified build contract'):
            self.admit()
        self.write_candidate(self.contract, 'ghcr.io/example/gateway', self.digest)
        self.revision = self.commit()
        with self.assertRaisesRegex(ValueError, 'immutable owner OCI image'):
            self.admit()

    def test_owner_build_is_bound_to_workflow_revision_and_attempt(self):
        for field, value in (('event', 'pull_request'), ('head_branch', 'feature'),
                             ('head_sha', 'f' * 40), ('run_attempt', 1),
                             ('path', '.github/workflows/other.yml'),
                             ('head_repository', {'full_name': 'example/gateway'})):
            with self.subTest(field=field):
                original = self.build[field]
                self.build[field] = value
                with self.assertRaisesRegex(ValueError, 'successful owner main Gateway package build'):
                    self.admit()
                self.build[field] = original

    def test_job_list_and_qualified_digest_fail_closed(self):
        self.jobs['total_count'] = 4
        with self.assertRaisesRegex(ValueError, 'Incomplete Gateway build job inventory'):
            self.admit()
        self.jobs['total_count'] = 3
        job = self.jobs['jobs'][-1]
        job['run_attempt'] = 1
        with self.assertRaisesRegex(ValueError, 'qualification must all succeed'):
            self.admit()
        job['run_attempt'] = 2
        job['name'] = 'Qualified OCI sha256:' + 'b' * 64
        with self.assertRaisesRegex(ValueError, 'qualified OCI digest is missing'):
            self.admit()


class ReleaseWorkflowTest(unittest.TestCase):
    def test_reusable_workflows_load_policy_from_their_own_reviewed_commit(self):
        workflows = Path(__file__).resolve().parents[1] / '.github/workflows'
        for name, permission in (('component-release.yml', {'contents': 'read'}),
                                 ('initial-oci-release.yml', {'contents': 'read', 'actions': 'read'})):
            document = yaml.load((workflows / name).read_text(), Loader=yaml.BaseLoader)
            self.assertEqual(document['permissions'], permission)
            for job in document['jobs'].values():
                checkouts = [step['with'] for step in job['steps'] if step.get('with', {}).get('path') == 'policy']
                with self.subTest(workflow=name, job=job['name']):
                    self.assertEqual(len(checkouts), 1)
                    self.assertEqual(checkouts[0]['repository'], 'hyperme-sh/spanopticon')
                    self.assertEqual(checkouts[0]['ref'], '${{ job.workflow_sha }}')
                    self.assertEqual(checkouts[0]['persist-credentials'], 'false')
