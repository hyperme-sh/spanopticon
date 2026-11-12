"""Offline preparation inputs; never stable admission or publication permission."""
from pathlib import Path
import hashlib
import re
import subprocess

from release.policy import contract_path, json_document, load_bundle, require


def prepare(candidate, provenance, source_root):
    """Check source and locked inputs alongside locally available artifact bytes.

    This deliberately makes no claim that the supplied bytes were built from the
    checkout. The owner build and consumer checks must establish that separately.
    """
    candidate = Path(candidate).resolve()
    source_root = Path(source_root).resolve()
    bundle, _ = load_bundle(candidate)
    record = json_document(provenance)
    require(isinstance(record, dict) and record.get('schema_version') == 1,
            'Unsupported preparation provenance')
    revision = record.get('source_revision')
    require(isinstance(revision, str) and re.fullmatch(r'[0-9a-f]{40}', revision),
            'Full preparation source revision required')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(source_root), *args],
                                       stderr=subprocess.DEVNULL).decode().strip()
    require(git('rev-parse', 'HEAD') == revision, 'Preparation source revision differs from checkout')
    require(not git('status', '--porcelain', '--untracked-files=no'),
            'Preparation source has tracked changes')
    locks = record.get('locks')
    require(isinstance(locks, dict) and locks, 'Dependency lock provenance required')
    for relative, digest in locks.items():
        path = contract_path(source_root, relative)
        require(git('ls-files', '--error-unmatch', '--', relative), 'Dependency lock must be tracked')
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest,
                'Dependency lock digest differs from source')
    artifacts = record.get('artifacts')
    require(isinstance(artifacts, dict) and set(artifacts) == set(bundle['components']),
            'Preparation artifact inventory must match candidate components')
    for name, component in bundle['components'].items():
        path = contract_path(candidate.parent, artifacts[name])
        require(hashlib.sha256(path.read_bytes()).hexdigest() == component['artifact']['sha256'],
                f'{name}: preparation artifact digest mismatch')
    return {'schema_version': 1, 'source_revision': revision,
            'components': sorted(bundle['components']), 'locks': locks,
            'purpose': 'preparation-inputs-only', 'publication_authorized': False}
