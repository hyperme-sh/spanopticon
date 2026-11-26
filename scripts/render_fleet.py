#!/usr/bin/env python3
"""Generate Compose, Helm and the resolved fleet inventory from an owner image."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployment import bundle
from deployment.fleet_inventory import generate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", default=Path("fleet/services.json"), type=Path)
    parser.add_argument("--qualified", required=True, type=Path)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        generate(bundle.document(args.inventory.read_bytes()), bundle.document(args.qualified.read_bytes()),
                 args.candidate, args.output)
    except (ValueError, OSError, TypeError, KeyError) as error:
        parser.exit(1, f"Fleet generation rejected: {error}\n")


if __name__ == "__main__":
    main()
