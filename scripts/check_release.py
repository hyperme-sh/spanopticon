#!/usr/bin/env python3
"""Validate an assembled release against the last accepted immutable bundle."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release.policy import PolicyError, validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    args = parser.parse_args()
    try:
        bundle = validate(args.previous, args.candidate)
    except (PolicyError, OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f'Release rejected: {error}\n')
    print(f"Release line {bundle['line']}: {len(bundle['components'])} components passed metadata/contract policy")


if __name__ == '__main__':
    main()
