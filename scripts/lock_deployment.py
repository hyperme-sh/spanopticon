#!/usr/bin/env python3
"""Verify downloaded OCI bundle blobs and write a digest-pinned deployment lock."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployment.bundle import write_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--layout', type=Path, required=True, help='Directory containing blobs/sha256/')
    parser.add_argument('--reference', required=True, help='Expected ghcr.io/owner/bundle@sha256:...')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        write_lock(args.layout, args.reference, args.output)
    except (ValueError, OSError, TypeError, KeyError, RecursionError) as error:
        parser.exit(1, f'Bundle rejected: {error}\n')
    print(args.output)


if __name__ == '__main__':
    main()
