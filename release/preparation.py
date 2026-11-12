"""Offline preparation inputs; never stable admission or publication permission."""
from pathlib import Path
import hashlib
import re
import subprocess

from release.policy import contract_path, json_document, load_bundle, require, validate
from release.accepted import verify_accepted_record
from release.installer import snapshot, compatible, verify_direct_urls


def prepare(candidate, provenance, source_root, *, baseline_root=None, repository=None, fetch=None):
    """Check source and locked inputs alongside locally available artifact bytes.

    This deliberately makes no claim that the supplied bytes were built from the
    checkout. The owner build and consumer checks must establish that separately.
    """
    candidate = Path(candidate).resolve()
    source_root = Path(source_root).resolve()
    bundle, _ = load_bundle(candidate)
    record = json_document(provenance)
    require(isinstance(record, dict) and type(record.get('schema_version')) is int and record['schema_version'] == 1,
            'Unsupported preparation provenance')
    revision = record.get('source_revision')
    require(isinstance(revision, str) and re.fullmatch(r'[0-9a-f]{40}', revision),
            'Full preparation source revision required')
    def git(*args):
        return subprocess.check_output(['git', '--literal-pathspecs', '-C', str(source_root), *args],
                                       stderr=subprocess.DEVNULL).decode().strip()
    require(git('rev-parse', 'HEAD') == revision, 'Preparation source revision differs from checkout')
    require(not git('status', '--porcelain', '--untracked-files=no'),
            'Preparation source has tracked changes')
    locks = record.get('locks')
    require(isinstance(locks, dict) and locks, 'Dependency lock provenance required')
    for relative, digest in locks.items():
        path = contract_path(source_root, relative)
        require(git('ls-files', '--error-unmatch', '--', relative), 'Dependency lock must be tracked')
        committed = subprocess.check_output(['git', '-C', str(source_root), 'show', revision + ':' + relative],
                                            stderr=subprocess.DEVNULL)
        require(hashlib.sha256(committed).hexdigest() == digest, 'Dependency lock differs from committed source')
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest,
                'Dependency lock digest differs from source')
    artifacts = record.get('artifacts')
    require(isinstance(artifacts, dict) and set(artifacts) == set(bundle['components']),
            'Preparation artifact inventory must match candidate components')
    installers = {}
    for name, component in bundle['components'].items():
        path = contract_path(candidate.parent, artifacts[name])
        require(hashlib.sha256(path.read_bytes()).hexdigest() == component['artifact']['sha256'],
                f'{name}: preparation artifact digest mismatch')
        if path.suffix == '.whl':
            installers[name] = snapshot(path)
            require(installers[name]['version'] == component['version'], 'Wheel version differs from candidate')
            verify_direct_urls(installers[name]['requires_dist'],
                record.get('direct_urls', {}).get(name, {}),
                record.get('installed_direct_urls', {}).get(name, {}), candidate.parent)
    if baseline_root is None and (source_root / 'releases/accepted').exists():
        baseline_root = source_root
    if baseline_root is not None:
        verify_accepted_record(baseline_root, repository, required=True, fetch=fetch)
        previous = Path(baseline_root) / 'releases/accepted/release.json'
        old, _ = load_bundle(previous)
        validate(previous, candidate)
        for name, installer in installers.items():
            require(name in old['components'], 'Installer baseline required for existing component')
            prior = contract_path(previous.parent, old['components'][name].get('installer_artifact'))
            require(hashlib.sha256(prior.read_bytes()).hexdigest() == old['components'][name]['artifact']['sha256'],
                    'Stable installer artifact digest mismatch')
            compatible(snapshot(prior), installer)
    return {'schema_version': 1, 'source_revision': revision,
            'components': sorted(bundle['components']), 'locks': locks, 'installers': installers,
            'baseline': 'verified-stable' if baseline_root is not None else 'none',
            'purpose': 'preparation-inputs-only', 'publication_authorized': False}
