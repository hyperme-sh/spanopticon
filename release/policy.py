"""Conservative, offline validation of coordinated release bundles."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
LINE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
NAME = re.compile(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
IMAGE = re.compile(r"[a-z0-9][a-z0-9._:/-]*@sha256:([0-9a-f]{64})\Z")


class PolicyError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise PolicyError(message)


def version(value):
    match = VERSION.fullmatch(value) if isinstance(value, str) else None
    require(match is not None, f"Invalid stable SemVer: {value!r}")
    return tuple(map(int, match.groups()))


def json_document(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    def invalid(value):
        raise PolicyError(f"Non-finite JSON number: {value}")
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique,
                      parse_constant=invalid)


def contract_path(root, value):
    require(isinstance(value, str) and value, "Contract path is required")
    path = Path(value)
    require(not path.is_absolute() and '..' not in path.parts, "Contract path must stay inside its bundle")
    resolved = (root / path).resolve()
    require(resolved.is_relative_to(root.resolve()), "Contract symlink escapes its bundle")
    require(resolved.is_file(), f"Missing contract: {value}")
    return resolved


def load_bundle(manifest):
    manifest = Path(manifest).resolve()
    data = json_document(manifest)
    require(isinstance(data, dict) and type(data.get('schema_version')) is int and data['schema_version'] == 1, 'Unsupported bundle schema')
    line = data.get('line')
    match = LINE.fullmatch(line) if isinstance(line, str) else None
    require(match is not None, 'Release line must be MAJOR.MINOR')
    line_tuple = tuple(map(int, match.groups()))
    components = data.get('components')
    require(isinstance(components, dict) and components, 'A release must contain components')
    contracts = {}
    for name, component in components.items():
        require(NAME.fullmatch(name) is not None, f'Invalid component name: {name}')
        require(isinstance(component, dict), f'{name}: component must be an object')
        require(version(component.get('version'))[:2] == line_tuple,
                f'{name}: version must use coordinated line {line}')
        digest = component.get('contract_sha256')
        require(isinstance(digest, str) and DIGEST.fullmatch(digest), f'{name}: invalid contract digest')
        path = contract_path(manifest.parent, component.get('contract'))
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, f'{name}: contract digest mismatch')
        document = json_document(path)
        require(isinstance(document, dict) and document, f'{name}: empty/non-object API contract')
        # A bundle can be a canonical OpenAPI document, protobuf descriptor JSON,
        # or a versioned aggregate of HTTP, event, configuration and library APIs.
        if 'openapi' in document:
            require(document.get('openapi') in ('3.0.0', '3.0.1', '3.0.2', '3.0.3', '3.1.0', '3.1.1'),
                    f'{name}: unsupported OpenAPI dialect')
            require(isinstance(document.get('info'), dict) and isinstance(document.get('paths'), dict),
                    f'{name}: incomplete OpenAPI document')
            require(document['info'].get('version') == component['version'],
                    f'{name}: exported API version differs from component version')
            document = json.loads(json.dumps(document))
            del document['info']['version']
        contracts[name] = document
        artifact = component.get('artifact')
        require(isinstance(artifact, dict), f'{name}: artifact metadata required')
        require(artifact.get('kind') in ('container', 'package'), f'{name}: unknown artifact kind')
        require(isinstance(artifact.get('sha256'), str) and DIGEST.fullmatch(artifact['sha256']),
                f'{name}: invalid artifact digest')
        require(isinstance(artifact.get('uri'), str) and artifact['uri'], f'{name}: artifact URI required')
        if artifact['kind'] == 'container':
            image = IMAGE.fullmatch(artifact['uri'])
            require(image and image.group(1) == artifact['sha256'], f'{name}: container digest/URI mismatch')
    return data, contracts


def same_json(left, right):
    # Python considers True == 1, but those are different API/schema values.
    return json.dumps(left, sort_keys=True, separators=(',', ':'), allow_nan=False) == json.dumps(
        right, sort_keys=True, separators=(',', ':'), allow_nan=False)


def compatible(old, new):
    """Conservative minor gate: only wholly new OpenAPI operations/schemas.

    Existing operations, schemas, auth, servers and all other fields must remain
    exactly equal. Other contract formats must remain equal until their own
    compatibility checker is implemented; unknown changes never pass by default.
    """
    if same_json(old, new):
        return True
    if not (isinstance(old.get('openapi'), str) and old.get('openapi') == new.get('openapi')):
        return False
    # Adding paths/schemas can affect references in an invalid baseline. Reject
    # any external or unresolved local references before considering additions.
    for document in (old, new):
        def visit(node):
            if isinstance(node, dict):
                if '$ref' in node:
                    ref = node['$ref']
                    if not isinstance(ref, str) or not ref.startswith('#/'):
                        return False
                    target = document
                    try:
                        for part in ref[2:].split('/'):
                            target = target[part.replace('~1', '/').replace('~0', '~')]
                    except (KeyError, TypeError):
                        return False
                return all(visit(v) for v in node.values())
            return not isinstance(node, list) or all(visit(v) for v in node)
        if not visit(document):
            return False
    # Only additive whole paths and unused new named schemas are admitted.
    # Changes to existing path objects (even new methods) require deeper review.
    def overlaps(left, right):
        a, b = left.strip('/').split('/'), right.strip('/').split('/')
        return len(a) == len(b) and all(x == y or '{' in x or '{' in y for x, y in zip(a, b))
    for path in set(new.get('paths', {})) - set(old.get('paths', {})):
        if any(overlaps(path, prior) for prior in old.get('paths', {})):
            return False
    stripped = json.loads(json.dumps(new))
    for key in set(new.get('paths', {})) - set(old.get('paths', {})):
        del stripped['paths'][key]
    old_schemas = old.get('components', {}).get('schemas', {})
    new_schemas = new.get('components', {}).get('schemas', {})
    for key in set(new_schemas) - set(old_schemas):
        del stripped['components']['schemas'][key]
    return same_json(stripped, old)


def validate(previous, candidate):
    old, old_contracts = load_bundle(previous)
    new, new_contracts = load_bundle(candidate)
    old_line = tuple(map(int, old['line'].split('.')))
    new_line = tuple(map(int, new['line'].split('.')))
    require(new_line >= old_line, 'Release line cannot go backwards')
    old_names, new_names = set(old['components']), set(new['components'])
    if old_line == new_line:
        require(old_names == new_names, 'Component membership changes require a coordinated minor release')
    elif old_line[0] == new_line[0]:
        require(old_names <= new_names, 'Removing components requires a major release')
    for name in old_names & new_names:
        before, after = old['components'][name], new['components'][name]
        require(version(after['version']) >= version(before['version']), f'{name}: version rollback')
        if before['version'] == after['version']:
            require(same_json(before['artifact'], after['artifact']) and same_json(old_contracts[name], new_contracts[name]),
                    f'{name}: existing version is immutable')
        if old_line == new_line:
            require(same_json(old_contracts[name], new_contracts[name]), f'{name}: patch changed the API contract')
        elif old_line[0] == new_line[0]:
            require(compatible(old_contracts[name], new_contracts[name]),
                    f'{name}: minor compatibility cannot be established; use a major release for breaking changes')
    return new
