"""Shared offline contract vectors; the release registry is explicitly mocked."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

from release.preparation import prepare
from release.installer import snapshot, verify_direct_urls


def write_json(path, value):
    path.write_text(json.dumps(value))


def exercise(case):
    with tempfile.TemporaryDirectory(prefix='preparation-contract-') as directory:
        root = Path(directory)
        source = root / 'source'
        source.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '-C', str(source), *args], stderr=subprocess.DEVNULL).decode().strip()
        git('init', '-q')
        git('config', 'user.name', 'Synthetic contract')
        git('config', 'user.email', 'contract@example.invalid')
        (source / 'requirements.lock').write_text('synthetic==1.0.0\n')
        git('add', '.')
        git('commit', '-qm', 'Synthetic source')
        revision = git('rev-parse', 'HEAD')
        def wheel(folder, version, python='>=3.11', tag='py3-none-any'):
            folder.mkdir(parents=True)
            path = folder / f'component-{version}-{tag}.whl'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr(f'component-{version}.dist-info/METADATA',
                    f'Metadata-Version: 2.3\nName: component\nVersion: {version}\n' +
                    (f'Requires-Python: {python}\n' if python is not None else '') + '\n')
                archive.writestr(f'component-{version}.dist-info/WHEEL',
                    f'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: {tag}\n\n')
                archive.writestr('component.py', 'VALUE = 1\n')
            return path
        def bundle(folder, version, python='>=3.11', tag='py3-none-any'):
            artifact = wheel(folder, version, python, tag)
            contract = b'{"synthetic_api":1}'
            (folder / 'contract.json').write_bytes(contract)
            component = {'version': version, 'contract': 'contract.json',
                         'contract_sha256': hashlib.sha256(contract).hexdigest(),
                         'installer_artifact': artifact.name,
                         'artifact': {'kind': 'package', 'uri': 'https://example.invalid/' + artifact.name,
                                      'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest()}}
            write_json(folder / 'release.json', {'schema_version': 1, 'line': '.'.join(version.split('.')[:2]), 'components': {'component': component}})
            return artifact, component
        artifact, component = bundle(root / 'candidate', '1.1.0' if case == 'new-component' else '1.0.1',
            None if case == 'optional-python' else '>=3.12' if case in {'changed-installer', 'renamed-wheel'} else '>=3.11',
            'cp312-cp312-manylinux_2_17_x86_64' if case == 'changed-wheel-tags' else 'py3-none-any')
        record = {'schema_version': 1, 'source_revision': revision,
                  'locks': {'requirements.lock': hashlib.sha256((source / 'requirements.lock').read_bytes()).hexdigest()},
                  'artifacts': {'component': artifact.name}}
        if case == 'new-component':
            declaration = json.loads((root / 'candidate/release.json').read_text())
            declaration['components']['additional'] = dict(component)
            write_json(root / 'candidate/release.json', declaration)
            record['artifacts']['additional'] = artifact.name
        if case == 'tampered-artifact': artifact.write_bytes(b'wrong')
        if case == 'missing-source': record.pop('source_revision')
        if case == 'missing-lock': record.pop('locks')
        if case == 'renamed-wheel':
            renamed = artifact.with_suffix('.bin')
            artifact.rename(renamed)
            artifact = renamed
            record['artifacts']['component'] = renamed.name
        provenance = root / 'candidate/preparation.json'
        write_json(provenance, record)
        options = {}
        if case in {'stable-baseline', 'test-baseline', 'changed-installer', 'renamed-wheel', 'new-component', 'changed-wheel-tags'}:
            baseline = root / 'baseline'
            old_artifact, old = bundle(baseline / 'releases/accepted', '1.0.0')
            tag = 'test-' + revision if case == 'test-baseline' else 'v1.0.0'
            write_json(baseline / 'releases/accepted/provenance.json', {
                'repository': 'example/component', 'revision': revision, 'release_id': 7, 'tag': tag,
                'assets': {old_artifact.name: 'sha256:' + old['artifact']['sha256']}})
            replies = {
                'repos/example/component/releases/7': {'id': 7, 'tag_name': tag, 'prerelease': False,
                    'draft': False, 'immutable': True,
                    'assets': [{'name': old_artifact.name, 'digest': 'sha256:' + old['artifact']['sha256']}]},
                'repos/example/component/git/ref/tags/v1.0.0': {
                    'ref': 'refs/tags/v1.0.0', 'object': {'type': 'commit', 'sha': revision}}}
            options = {'baseline_root': baseline, 'repository': 'example/component', 'fetch': replies.__getitem__}
        result = prepare(root / 'candidate/release.json', provenance, source, **options)
        if case in {'verified-direct-url', 'wrong-direct-url'}:
            digest = component['artifact']['sha256']
            url = 'https://example.invalid/' + artifact.name + '#sha256=' + digest
            installed = {'component': {'url': url.split('#')[0], 'archive_info': {'hashes': {'sha256': digest}}}}
            if case == 'wrong-direct-url': installed['component']['url'] = 'https://other.invalid/package.whl'
            verify_direct_urls(['component @ ' + url],
                {'component': {'url': url, 'sha256': digest, 'path': artifact.name}}, installed, artifact.parent)
        return result


def run_vectors(vectors):
    outcomes = []
    for vector in vectors:
        try:
            exercise(vector['case'])
            accepted = True
        except ValueError:
            accepted = False
        if accepted is not vector['accepted']:
            raise AssertionError('Unexpected preparation outcome: ' + vector['case'])
        outcomes.append({'case': vector['case'], 'accepted': accepted})
    return outcomes
