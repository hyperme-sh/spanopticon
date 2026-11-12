#!/usr/bin/env python3
"""Scheduled-only fresh template archive consumer and shared policy vectors."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release.preparation import prepare
from release.policy import require


def qualify(root, output):
    require(os.environ.get('GITHUB_EVENT_NAME') == 'schedule' and
            os.environ.get('GITHUB_REPOSITORY') == 'hyperme-sh/spanopticon' and
            os.environ.get('NIGHTLY_SUITE') == 'release-preparation-source', 'Central scheduled batch required')
    root, output = Path(root).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD']).decode().strip()
    require(not subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=no']),
            'Fresh archive requires clean source')
    with tempfile.TemporaryDirectory(prefix='release-preparation-') as temporary:
        scratch = Path(temporary)
        archive_path = scratch / 'spanopticon-templates.tar.gz'
        with archive_path.open('wb') as stream:
            subprocess.run(['git', '-C', str(root), 'archive', '--format=tar.gz', revision], stdout=stream, check=True)
        # The template bundle has no embedded package version. 0.0.0 below is
        # only the synthetic policy fixture's version, never a publication label.
        contract = b'{"fixture":"current-source-template-archive"}'
        (scratch / 'contract.json').write_bytes(contract)
        (scratch / 'release.json').write_text(json.dumps({'schema_version': 1, 'line': '0.0', 'components': {
            'template-fixture': {'version': '0.0.0', 'contract': 'contract.json',
                'contract_sha256': hashlib.sha256(contract).hexdigest(), 'artifact': {'kind': 'package',
                    'uri': 'urn:local:spanopticon-templates', 'sha256': hashlib.sha256(archive_path.read_bytes()).hexdigest()}}}}))
        (scratch / 'preparation.json').write_text(json.dumps({'schema_version': 1, 'source_revision': revision,
            'locks': {'requirements-deployment.txt': hashlib.sha256((root / 'requirements-deployment.txt').read_bytes()).hexdigest()},
            'artifacts': {'template-fixture': archive_path.name}}))
        result = prepare(scratch / 'release.json', scratch / 'preparation.json', root)
        consumer = scratch / 'consumer'
        with tarfile.open(archive_path) as archive:
            archive.extractall(consumer, filter='data')
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        subprocess.run([sys.executable, '-m', 'pytest', '-c', '/dev/null',
            'tests/test_preparation.py', 'tests/test_preparation_vectors.py', 'tests/test_installer.py',
            '--junitxml=' + str(output / 'preparation.xml')], cwd=consumer, env=env, check=True)
        result['archive_sha256'] = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        result['consumer'] = 'freshly archived Spanopticon template source; synthetic wheel and mocked registry vectors'
        (output / 'preparation.json').write_text(json.dumps(result, sort_keys=True) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    qualify(Path(__file__).resolve().parents[1], args.output)
