"""Verify a downloaded OCI bundle before writing an immutable deployment lock."""
import hashlib
import json
from pathlib import Path
import re

OCI = 'application/vnd.oci.image.manifest.v1+json'
EMPTY = 'application/vnd.oci.empty.v1+json'
BUNDLE = 'application/vnd.spanopticon.deployment.bundle.v1+json'
ASSETS = {'compose': 'application/vnd.spanopticon.compose.v1+json',
          'helm': 'application/vnd.cncf.helm.chart.content.v1.tar+gzip'}
DIGEST = r'sha256:[0-9a-f]{64}'
REPOSITORY = r'ghcr\.io/[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+'
VERSION = r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)'
MAX_METADATA = 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def document(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Non-JSON numeric constant: ' + value)
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    require(isinstance(value, dict), 'Expected a JSON object')
    return value


def keys(value, expected, label):
    require(isinstance(value, dict) and set(value) == set(expected), 'Invalid ' + label + ' fields')


def descriptor(value, media_type):
    keys(value, ('mediaType', 'digest', 'size'), 'descriptor')
    require(value['mediaType'] == media_type, 'Unexpected descriptor media type')
    require(matches(DIGEST, value['digest']), 'Expected a SHA-256 descriptor')
    require(type(value['size']) is int and 0 < value['size'] <= MAX_TOTAL, 'Invalid descriptor size')


def blob(root, digest, limit, expected_size=None, retain=True):
    require(matches(DIGEST, digest), 'Invalid blob digest')
    path = root / 'blobs' / 'sha256' / digest.split(':')[1]
    require(not any(p.is_symlink() for p in (root / 'blobs', root / 'blobs/sha256', path)), 'Symlink in blob path')
    require(path.is_file(), 'Missing regular blob: ' + digest)
    size = path.stat().st_size
    require(0 < size <= limit and (expected_size is None or size == expected_size), 'Blob size mismatch or limit exceeded')
    sha = hashlib.sha256()
    chunks = []
    total = 0
    with path.open('rb') as stream:
        while chunk := stream.read(64 * 1024):
            total += len(chunk)
            require(total <= limit, 'Blob grew beyond size limit')
            sha.update(chunk)
            if retain:
                chunks.append(chunk)
    require(total == size and 'sha256:' + sha.hexdigest() == digest, 'Blob digest or size mismatch')
    return b''.join(chunks)


def verify(layout, reference):
    """Check local bytes only. The expected reference must come from a trusted resolver."""
    require(matches(REPOSITORY + '@' + DIGEST, reference), 'Expected a digest-pinned GHCR bundle reference')
    root = Path(layout).resolve()
    manifest_digest = reference.rsplit('@', 1)[1]
    manifest = document(blob(root, manifest_digest, MAX_METADATA))
    keys(manifest, ('schemaVersion', 'mediaType', 'artifactType', 'config', 'layers'), 'OCI manifest')
    require(type(manifest['schemaVersion']) is int and manifest['schemaVersion'] == 2
            and manifest['mediaType'] == OCI and manifest['artifactType'] == BUNDLE, 'Unsupported OCI bundle manifest')
    descriptor(manifest['config'], EMPTY)
    require(blob(root, manifest['config']['digest'], MAX_METADATA, manifest['config']['size']) == b'{}', 'Expected OCI empty config')
    layers = manifest['layers']
    require(isinstance(layers, list) and len(layers) == 3, 'Expected bundle metadata, Compose and Helm layers')
    by_type = {}
    for layer in layers:
        require(isinstance(layer, dict) and layer.get('mediaType') in (BUNDLE, *ASSETS.values()), 'Unsupported bundle layer')
        descriptor(layer, layer['mediaType'])
        require(layer['mediaType'] not in by_type, 'Duplicate bundle layer type')
        by_type[layer['mediaType']] = layer
    require(sum(layer['size'] for layer in layers) <= MAX_TOTAL, 'Total bundle size limit exceeded')
    metadata = by_type[BUNDLE]
    bundle = document(blob(root, metadata['digest'], MAX_METADATA, metadata['size']))
    keys(bundle, ('schema', 'candidate', 'source_revision', 'components', 'deployment_assets'), 'bundle')
    require(type(bundle['schema']) is int and bundle['schema'] == 1, 'Unsupported bundle schema')
    require(matches(VERSION + r'-test\.(0|[1-9][0-9]*)', bundle['candidate']), 'Expected immutable test candidate identity')
    require(matches(r'[0-9a-f]{40}', bundle['source_revision']), 'Expected source commit')
    components = bundle['components']
    require(isinstance(components, dict) and 0 < len(components) <= 128, 'Expected bounded nonempty component set')
    for name, component in components.items():
        require(matches(r'[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?', name), 'Invalid component name')
        keys(component, ('image', 'version', 'source_revision'), 'component')
        require(matches(REPOSITORY + '@' + DIGEST, component['image']), 'Component image must be pinned to a GHCR digest')
        require(matches(r'[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}', component['version']), 'Invalid embedded component version')
        require(matches(r'[0-9a-f]{40}', component['source_revision']), 'Expected component source commit')
    keys(bundle['deployment_assets'], ASSETS, 'deployment assets')
    for name, media_type in ASSETS.items():
        declared = bundle['deployment_assets'][name]
        descriptor(declared, media_type)
        require(declared == by_type[media_type], 'Deployment asset descriptor differs from OCI layer')
        blob(root, declared['digest'], MAX_TOTAL, declared['size'], retain=False)
    return {'schema': 1, 'verification': 'local-content-only', 'bundle_reference': reference,
            'metadata_digest': metadata['digest'], **{key: bundle[key] for key in
            ('candidate', 'source_revision', 'components', 'deployment_assets')}}


def write_lock(layout, reference, output):
    lock = verify(layout, reference)
    # Invalid input never creates output; an existing file or symlink is not replaced.
    with Path(output).open('x', encoding='utf-8') as stream:
        json.dump(lock, stream, sort_keys=True, indent=2)
        stream.write('\n')
    return lock
