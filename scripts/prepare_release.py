#!/usr/bin/env python3
"""Verify local first-build inputs without inventing a stable release baseline."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release.preparation import prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('candidate', 'provenance', 'source-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.candidate, args.provenance, args.source_root)
    except (ValueError, TypeError, KeyError, OSError, subprocess.CalledProcessError):
        parser.exit(1, 'Preparation rejected: invalid source, lock, contract or artifact inputs\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
