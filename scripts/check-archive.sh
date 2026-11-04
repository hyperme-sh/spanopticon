#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
mkdir -p "$scratch/package"
git archive --format=tar.gz HEAD > "$scratch/spanopticon-templates.tar.gz"
tar -xzf "$scratch/spanopticon-templates.tar.gz" -C "$scratch/package"
(cd "$scratch/package" && python3 -m unittest discover -s tests -v)
if [[ -n "${RELEASE_ARTIFACT_DIR:-}" ]]; then
  mkdir -p "$RELEASE_ARTIFACT_DIR"
  cp "$scratch/spanopticon-templates.tar.gz" "$RELEASE_ARTIFACT_DIR/"
fi
