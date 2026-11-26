#!/usr/bin/env python3
"""Publish and resolve the three named Spanopticon configuration bundles."""

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deployment.enterprise_bundle import PROFILES, publish, repository, resolve
from deployment.publish import Registry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("publish")
    fetch = commands.add_parser("resolve")
    fetch.add_argument("profile", choices=PROFILES)
    fetch.add_argument("selector")
    args = parser.parse_args()
    try:
        if args.command == "publish":
            result = publish(ROOT / "profiles/enterprise")
        else:
            registry = Registry(repository(args.profile), os.environ.get("GHCR_USERNAME"), os.environ.get("GHCR_TOKEN"))
            result = resolve(args.profile, args.selector, registry)
        print(json.dumps(result, indent=2, sort_keys=True))
    except (OSError, TypeError, ValueError, KeyError) as error:
        parser.exit(1, f"Enterprise configuration rejected: {error}\n")


if __name__ == "__main__":
    main()
