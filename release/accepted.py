"""Verify that an accepted component baseline is an actual stable release.

Development snapshots can qualify artifacts, but cannot reserve a stable version.
The GitHub API, rather than candidate-controlled provenance flags, establishes
the release channel, immutable tag target and published asset digests.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from release.policy import PolicyError, json_document, load_bundle, require

SHA = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def github_json(path):
    """Read only the fixed GitHub API origin, without credential redirects."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GH_TOKEN", "")
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request("https://api.github.com/" + path, headers=headers)
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=10) as response:
            raw = response.read(1024 * 1024 + 1)
        require(len(raw) <= 1024 * 1024, "GitHub release response exceeds limit")
        value = json.loads(raw)
        require(isinstance(value, dict), "Invalid GitHub release response")
        return value
    except (OSError, ValueError, urllib.error.HTTPError):
        raise PolicyError("Unable to verify accepted stable release with GitHub") from None


def verify_accepted_record(root, repository, *, required=False, fetch=None):
    """Return the stable version, or None when no accepted record is present."""
    root = Path(root).resolve()
    require(isinstance(repository, str) and REPOSITORY.fullmatch(repository), "Invalid repository identity")
    folder = root / "releases/accepted"
    manifest = folder / "release.json"
    provenance_path = folder / "provenance.json"
    if not manifest.exists() and not provenance_path.exists():
        require(not required, "No accepted stable release; test qualification is not a stable baseline")
        return None
    require(manifest.is_file() and provenance_path.is_file(), "Accepted stable release requires manifest and provenance")
    require(manifest.resolve().is_relative_to(root) and provenance_path.resolve().is_relative_to(root), "Accepted record escapes checkout")
    bundle, _ = load_bundle(manifest)
    name = repository.split("/")[1]
    require(set(bundle["components"]) == {name}, "Accepted record must contain its owning component")
    component = bundle["components"][name]
    provenance = json_document(provenance_path)
    require(isinstance(provenance, dict) and provenance.get("repository") == repository, "Accepted provenance repository mismatch")
    tag = "v" + component["version"]
    require(provenance.get("tag") == tag, "Accepted baseline requires a stable version tag; test snapshots are qualification only")
    revision = provenance.get("revision")
    require(isinstance(revision, str) and SHA.fullmatch(revision), "Accepted source revision must be immutable")
    release_id = provenance.get("release_id")
    require(type(release_id) is int and release_id > 0, "Accepted stable release ID required")
    fetch = fetch or github_json
    release = fetch(f"repos/{repository}/releases/{release_id}")
    require(release.get("id") == release_id and release.get("tag_name") == tag, "Published release identity mismatch")
    require(release.get("prerelease") is False and release.get("draft") is False,
            "A prerelease or draft cannot be an accepted stable baseline")
    require(release.get("immutable") is True, "Accepted stable release must be immutable")
    reference = fetch(f"repos/{repository}/git/ref/tags/{tag}")
    require(reference.get("ref") == "refs/tags/" + tag, "Stable tag reference mismatch")
    target = reference.get("object", {})
    seen = set()
    for _ in range(4):
        sha = target.get("sha")
        require(isinstance(sha, str) and SHA.fullmatch(sha) and sha not in seen, "Invalid stable tag target")
        seen.add(sha)
        if target.get("type") == "commit":
            require(sha == revision, "Stable tag source differs from accepted provenance")
            break
        require(target.get("type") == "tag", "Unsupported stable tag target")
        target = fetch(f"repos/{repository}/git/tags/{sha}").get("object", {})
    else:
        raise PolicyError("Stable tag chain exceeds limit")
    assets = release.get("assets")
    expected = provenance.get("assets")
    require(isinstance(assets, list) and isinstance(expected, dict) and expected, "Published asset provenance required")
    published = {}
    for asset in assets:
        require(isinstance(asset, dict) and isinstance(asset.get("name"), str), "Invalid published asset")
        require(asset["name"] not in published, "Duplicate published asset")
        published[asset["name"]] = asset.get("digest")
    for asset_name, digest in expected.items():
        require(isinstance(asset_name, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", asset_name), "Invalid accepted asset name")
        require(isinstance(digest, str) and DIGEST.fullmatch(digest), "Accepted asset digest required")
        require(published.get(asset_name) == digest, "Accepted asset differs from the stable published release")
    require("sha256:" + component["artifact"]["sha256"] in expected.values(), "Accepted component artifact is absent from stable asset provenance")
    return component["version"]
