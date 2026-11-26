#!/usr/bin/env python3
"""Resolve the Portal owner's qualified TEST image for fleet generation."""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployment import bundle
from deployment.fleet_inventory import generate, inspect_inventory
from deployment.publish import REPOSITORY, Registry
from deployment.registry import fetch_test
from deployment.runtime import command

NEXT_VERSION = "0.24.0"


def resolve(portal, execute=command):
    selector = f'{portal["repository"]}:{portal["channel"]}'
    digest = execute(["docker", "buildx", "imagetools", "inspect", selector,
                      "--format", "{{.Manifest.Digest}}"]).decode().strip()
    bundle.require(bundle.matches(bundle.DIGEST, digest), "Portal owner TEST did not resolve to an OCI digest")
    image = f'{portal["repository"]}@{digest}'
    execute(["docker", "pull", "--quiet", image])
    source = execute(["docker", "image", "inspect", image, "--format",
                      '{{index .Config.Labels "org.opencontainers.image.revision"}}']).decode().strip()
    version = execute(["docker", "run", "--rm", "--read-only", "--network", "none", "--cap-drop", "ALL",
                       "--entrypoint", "node", image, "-p", 'require("/app/package.json").version']).decode().strip()
    return {"selector": selector, "image": image, "version": version, "source_revision": source}


def reconcile(inventory, output, scratch, run_id, source_revision, registry, execute=command):
    bundle.require(bundle.matches(r"[1-9][0-9]*", run_id), "Invalid GitHub run identifier")
    bundle.require(bundle.matches(r"[0-9a-f]{40}", source_revision), "Invalid Spanopticon source revision")
    portal = inspect_inventory(inventory)
    qualified = resolve(portal, execute)
    current = registry.optional_manifest("test")
    if current is not None:
        receipt = fetch_test(REPOSITORY, "test", NEXT_VERSION, scratch / "previous", registry)
        bundle.require(receipt["bundle_reference"] == f"{REPOSITORY}@{current}",
                       "Fleet TEST changed during resolution")
        lock = bundle.verify(scratch / "previous", receipt["bundle_reference"])
        component = {key: qualified[key] for key in ("image", "version", "source_revision")}
        if lock["source_revision"] == source_revision and lock["components"] == {"portal": component}:
            return False
    generate(inventory, qualified, f"{NEXT_VERSION}-test.{run_id}", output)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--scratch", required=True, type=Path)
    args = parser.parse_args()
    try:
        registry = Registry(REPOSITORY, os.environ["GHCR_USERNAME"], os.environ["GHCR_TOKEN"])
        print(str(reconcile(bundle.document(args.inventory.read_bytes()), args.output,
                            args.scratch, os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_SHA"], registry)).lower())
    except (ValueError, OSError, TypeError, KeyError) as error:
        parser.exit(1, f"Portal fleet resolution rejected: {error}\n")


if __name__ == "__main__":
    main()
