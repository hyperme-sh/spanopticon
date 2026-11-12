"""Supplement owner API exports with conservative installation contracts."""
import hashlib
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, parse_qs, unquote
import zipfile

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from release.policy import require, contract_path


def is_wheel(path, uri):
    if str(path).lower().endswith('.whl') or unquote(urlsplit(uri).path).lower().endswith('.whl'):
        return True
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            return any('.dist-info/' in name for name in archive.namelist())
    return False


def snapshot(wheel):
    """Read metadata without importing the candidate or extracting its paths."""
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), 'Duplicate wheel member')
        require(all(not n.startswith('/') and '\\' not in n and '..' not in PurePosixPath(n).parts for n in names),
                'Unsafe wheel member')
        metadata_names = [n for n in names if n.endswith('.dist-info/METADATA')]
        require(len(metadata_names) == 1, 'Expected one wheel distribution')
        prefix = metadata_names[0].removesuffix('METADATA')
        require(all('.dist-info/' not in n or n.startswith(prefix) for n in names), 'Mixed wheel distributions')
        def read(name):
            require(archive.getinfo(name).file_size <= 1024 * 1024, 'Oversized installer metadata')
            return archive.read(name)
        metadata = BytesParser().parsebytes(read(metadata_names[0]))
        for field in ('Name', 'Version', 'Requires-Python'):
            require(len(metadata.get_all(field, [])) == 1, 'Missing or ambiguous installer identity')
        canonicalize_name(metadata['Name'], validate=True)
        SpecifierSet(metadata['Requires-Python'])
        requirements = sorted(str(Requirement(r)) for r in metadata.get_all('Requires-Dist', []))
        entries = read(prefix + 'entry_points.txt') if prefix + 'entry_points.txt' in names else b''
        return {'name': canonicalize_name(metadata['Name']), 'version': metadata['Version'],
                'requires_python': metadata['Requires-Python'], 'requires_dist': requirements,
                'provides_extra': sorted(metadata.get_all('Provides-Extra', [])),
                'entry_points_sha256': hashlib.sha256(entries).hexdigest()}


def compatible(before, after):
    """Conservative installer compatibility: version may change, other fields may not."""
    require(set(before) == set(after) == {'name', 'version', 'requires_python', 'requires_dist',
                                         'provides_extra', 'entry_points_sha256'},
            'Incomplete installer contract')
    require({k: v for k, v in before.items() if k != 'version'} ==
            {k: v for k, v in after.items() if k != 'version'}, 'Incompatible installer metadata')


def verify_direct_urls(requirements, locked, installed, artifact_root, *, extras=()):
    """Bind URL requirements to locked bytes and installed PEP 610 records.

    VCS dependencies remain the responsibility of the owner's existing fixture
    verifier; this function refuses them instead of silently treating them as URLs.
    """
    required = {}
    for raw in requirements:
        requirement = Requirement(raw)
        if requirement.marker and not any(requirement.marker.evaluate({'extra': extra}) for extra in {'', *extras}):
            continue
        if not requirement.url:
            continue
        name = canonicalize_name(requirement.name)
        require(name not in required or required[name] == requirement.url, 'Conflicting direct URL requirements')
        required[name] = requirement.url
    require(set(locked) == set(installed) == set(required), 'Direct URL provenance inventory mismatch')
    for name, url in required.items():
        parsed = urlsplit(url)
        require(parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password,
                'Unsupported direct URL origin')
        record = locked[name]
        digest = record.get('sha256')
        require(isinstance(digest, str) and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest),
                'Locked direct URL digest required')
        require(record.get('url') == url, 'Direct URL differs from lock')
        require(parse_qs(parsed.fragment) == {'sha256': [digest]}, 'Direct URL requires its locked SHA-256 fragment')
        path = contract_path(Path(artifact_root), record.get('path'))
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, 'Direct URL artifact digest mismatch')
        actual = installed[name]
        require(actual.get('url') == url.split('#', 1)[0] and
                actual.get('archive_info', {}).get('hashes', {}).get('sha256') == digest and
                not any(k in actual for k in ('vcs_info', 'dir_info')), 'Installed direct URL provenance mismatch')
