#!/usr/bin/env python3
"""Generate enterprise Compose, Helm and runtime input references from the fleet inventory."""
import argparse
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deployment import bundle
from deployment.fleet_inventory import generate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chart-version", required=True)
    parser.add_argument("--output", required=True, help="New directory name in the working directory")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", args.output):
        parser.error("Expected a single new output directory name")
    try:
        inventory = bundle.document((ROOT / "deployment/enterprise-services.json").read_bytes())
        assets = generate(inventory, args.chart_version)
        output = Path.cwd() / args.output
        output.mkdir(mode=0o700)
        for name, data in assets.items():
            with (output / name).open("xb") as stream:
                stream.write(data)
    except (ValueError, OSError, TypeError, KeyError) as error:
        parser.exit(1, f"Fleet generation rejected: {error}\n")


if __name__ == "__main__":
    main()
