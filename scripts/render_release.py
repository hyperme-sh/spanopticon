#!/usr/bin/env python3
"""Render generic deployment files only after coordinated release validation."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release.policy import PolicyError, contract_path, require, validate
from new_plane import manifests


def render(previous, candidate, output):
    bundle = validate(previous, candidate)
    root = Path(candidate).resolve().parent
    prepared = {}
    exported = copy.deepcopy(bundle)
    for name, component in bundle['components'].items():
        raw = contract_path(root, component['contract']).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == component['contract_sha256'],
                f'{name}: contract changed while rendering')
        relative = f'contracts/{name}.json'
        prepared[relative] = raw
        exported['components'][name]['contract'] = relative
        if component['artifact']['kind'] == 'container':
            for filename, document in manifests(name, component['artifact']['uri']).items():
                if filename == 'compose.yaml':
                    document['services'][name]['labels'] = {'org.opencontainers.image.version': component['version']}
                else:
                    for item in document['items']:
                        item.setdefault('metadata', {}).setdefault('annotations', {})['spanopticon.io/version'] = component['version']
                prepared[f'{name}/{filename}'] = (json.dumps(document, indent=2) + '\n').encode()
    require(any(path.endswith('/compose.yaml') for path in prepared), 'No container components to render')
    output = Path(output)
    output.mkdir()  # Refuse files, directories and symlinks, including empty ones.
    for relative, content in prepared.items():
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(content)
    # Write the manifest last; incomplete output must not look like a full bundle.
    with (output / 'release.json').open('x', encoding='utf-8') as stream:
        json.dump(exported, stream, indent=2)
        stream.write('\n')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(render(args.previous, args.candidate, args.output))
    except (PolicyError, OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f'Render rejected: {error}\n')


if __name__ == '__main__':
    main()
