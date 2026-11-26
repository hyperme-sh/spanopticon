#!/usr/bin/env python3
"""Generate enterprise Compose, Helm and runtime input references from the fleet inventory."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployment import bundle
from deployment.fleet_inventory import generate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", default=Path("deployment/enterprise-services.json"), type=Path)
    parser.add_argument("--chart-version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        generate(bundle.document(args.inventory.read_bytes()), args.chart_version, args.output)
    except (ValueError, OSError, TypeError, KeyError) as error:
        parser.exit(1, f"Fleet generation rejected: {error}\n")


if __name__ == "__main__":
    main()
