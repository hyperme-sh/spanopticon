"""Reject provider-specific symbols on the declared open runtime surface.

Pass one or more runtime/package paths explicitly; missing inputs fail closed.
Scan code, schemas, templates and dependency metadata, not prose or test evidence.
Requirements pins and lockfiles are dependency metadata and are never skipped.
The caller must enumerate its complete shipped surface (including generated SDKs).
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re

PROVIDER = re.compile(r'stripe', re.IGNORECASE)
EXTENSIONS = {'.py','.pyi','.go','.rs','.js','.jsx','.ts','.tsx','.proto','.sql',
              '.json','.yaml','.yml','.toml','.jinja','.j2','.h','.hpp','.c','.cpp',
              '.kt','.kts','.java','.swift','.cs'}
# Manifests whose suffix is not a source extension. *.lock is matched separately.
DEPENDENCY_NAMES = {
    'pipfile','gemfile','go.mod','go.sum','gradle.lockfile','package.resolved',
    'packages.config','bun.lockb','constraints.txt','pom.xml','build.gradle','setup.cfg',
}


def violations(paths):
    found = []
    for root in paths:
        if not root.exists():
            raise ValueError(f'missing open surface: {root}')
        files = [root] if root.is_file() else sorted(root.rglob('*'))
        for path in files:
            if not include(path):
                continue
            for line, value in enumerate(text_lines(path), 1):
                if PROVIDER.search(value):
                    found.append(f'{path}:{line}: provider-specific symbol in open surface')
    return found


def include(path):
    if not path.is_file():
        return False
    if path.suffix.lower() in EXTENSIONS:
        return True
    return dependency_metadata(path)


def dependency_metadata(path):
    name = path.name.lower()
    suffix = path.suffix.lower()
    if suffix == '.lock':
        return True
    if name in DEPENDENCY_NAMES:
        return True
    return suffix in {'.txt', '.in'} and (
        'requirements' in name or path.parent.name.lower() == 'requirements')


def text_lines(path):
    data = path.read_bytes()
    try:
        return data.decode('utf-8').splitlines()
    except UnicodeError:
        if path.suffix.lower() in EXTENSIONS:
            raise
        # Lockfiles such as bun.lockb are not UTF-8; still search them.
        return data.decode('latin-1').splitlines()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='+', type=Path)
    args = parser.parse_args()
    try:
        failures = violations(args.paths)
    except (ValueError, OSError, UnicodeError) as exc:
        parser.exit(2, str(exc) + '\n')
    if failures:
        parser.exit(1, '\n'.join(failures) + '\n')


if __name__ == '__main__':
    main()
