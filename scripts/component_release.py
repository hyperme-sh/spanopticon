#!/usr/bin/env python3
"""Bind release admission to owner package versions and a trusted baseline."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release.policy import PolicyError, contract_path, json_document, require, validate, version


def configuration(root, repository, line):
    root = Path(root).resolve()
    config = json_document(contract_path(root, 'release-policy.json'))
    require(config.get('schema_version') == 1, 'Unsupported component policy')
    require(config.get('repository') == repository, 'Policy repository identity mismatch')
    require(config.get('line') == line, 'Policy differs from synchronized release line')
    name = repository.split('/')[-1]
    require(config.get('component') == name, 'Policy component identity mismatch')
    files = config.get('version_files')
    require(isinstance(files, list) and files and len(files) == len(set(files)), 'Version files must be a nonempty unique list')
    versions = {}
    for relative in files:
        path = contract_path(root, relative)
        if path.name == 'pyproject.toml':
            value = tomllib.loads(path.read_text())['project']['version']
        elif path.name == 'package.json':
            value = json_document(path)['version']
        else:
            raise PolicyError('Unsupported version source')
        version(value)
        versions[relative] = value
    return config, versions


def release_required(current, previous, repository, line):
    _, versions = configuration(current, repository, line)
    # A candidate submission always needs admission, even without a version bump.
    if (Path(current) / 'releases/candidate/release.json').exists():
        return True
    for relative, value in versions.items():
        path = contract_path(Path(previous).resolve(), relative)
        old = (tomllib.loads(path.read_text())['project']['version']
               if path.name == 'pyproject.toml' else json_document(path)['version'])
        if value != old:
            return True
    return False


def admit(current, baseline, repository, line, requested_version=None):
    config, versions = configuration(current, repository, line)
    accepted = contract_path(Path(baseline).resolve(), 'releases/accepted/release.json')
    candidate = contract_path(Path(current).resolve(), 'releases/candidate/release.json')
    bundle = validate(accepted, candidate)
    require(bundle['line'] == line, 'Candidate is outside the synchronized release line')
    require(set(bundle['components']) == {config['component']}, 'Owner candidate must contain exactly its component')
    component = bundle['components'][config['component']]
    require(all(v == component['version'] for v in versions.values()),
            'Candidate version differs from an owning package version')
    if requested_version:
        requested_version = requested_version.removeprefix('v')
        version(requested_version)
        require(component['version'] == requested_version, 'Requested release/tag version differs from candidate')
    return component['version']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository-root', type=Path, required=True)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--line', required=True)
    parser.add_argument('--previous-source', type=Path)
    parser.add_argument('--baseline-root', type=Path)
    parser.add_argument('--baseline-ref')
    parser.add_argument('--requested-version')
    parser.add_argument('--configuration-only', action='store_true')
    args = parser.parse_args()
    try:
        if args.configuration_only:
            require(args.previous_source is not None, 'Previous source checkout required')
            needed = release_required(args.repository_root, args.previous_source, args.repository, args.line)
            output = f'release-required={str(needed).lower()}\n'
            if os.environ.get('GITHUB_OUTPUT'):
                with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
                    stream.write(output)
            print(output.strip())
            print('Configuration checked only; this is not release acceptance.')
        else:
            require(isinstance(args.baseline_ref, str) and re.fullmatch('[0-9a-f]{40}', args.baseline_ref),
                    'Set SPANOPTICON_RELEASE_BASELINE_SHA to the immutable reviewed accepted-bundle commit; bootstrap is required')
            require(args.baseline_root is not None, 'Accepted baseline checkout required')
            accepted = admit(args.repository_root, args.baseline_root, args.repository, args.line, args.requested_version)
            print(f'{args.repository} {accepted}: declaration-level release admission passed; artifact/consumer qualification remains required')
    except (PolicyError, OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f'Release admission rejected: {error}\n')


if __name__ == '__main__':
    main()
