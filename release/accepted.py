"""Verify that an accepted component baseline is an actual stable release.

Development snapshots can qualify artifacts, but cannot reserve a stable version.
The GitHub API, rather than candidate-controlled provenance flags, establishes
the release channel, immutable tag target and published asset digests.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse
from pathlib import Path

from release.policy import PolicyError, json_document, load_bundle, require

SHA = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def github_json(path, *, document_type=dict):
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
        require(isinstance(value, document_type), "Invalid GitHub release response")
        return value
    except (OSError, ValueError, urllib.error.HTTPError):
        raise PolicyError("Unable to verify accepted stable release with GitHub") from None


def github_asset(repository, asset_id):
    """Read a digest-bound provenance attachment; never forward API credentials."""
    require(REPOSITORY.fullmatch(repository) and type(asset_id) is int and asset_id > 0,
            "Invalid provenance asset identity")
    headers = {"Accept": "application/octet-stream", "X-GitHub-Api-Version": "2022-11-28"}
    if os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    opener = urllib.request.build_opener(_NoRedirect())
    request = urllib.request.Request(f"https://api.github.com/repos/{repository}/releases/assets/{asset_id}", headers=headers)
    try:
        try:
            response = opener.open(request, timeout=10)
        except urllib.error.HTTPError as error:
            location = error.headers.get("Location", "")
            code = error.code
            error.close()
            target = urlparse(location)
            require(code in {302, 303, 307} and target.scheme == "https"
                    and target.hostname == "release-assets.githubusercontent.com"
                    and target.port in {None, 443} and not target.username
                    and not target.password and not target.fragment,
                    "Untrusted provenance asset redirect")
            # The signed CDN URL is obtained from the fixed GitHub API origin.
            # It receives no API token, cookies or caller-controlled headers.
            response = opener.open(urllib.request.Request(location, headers={"Accept": "application/octet-stream"}), timeout=10)
        with response:
            raw = response.read(1024 * 1024 + 1)
        require(len(raw) <= 1024 * 1024, "Provenance attachment exceeds limit")
        return raw
    except (OSError, ValueError):
        raise PolicyError("Unable to verify published container provenance") from None


def verify_accepted_record(root, repository, *, required=False, fetch=None, fetch_asset=None):
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
    published_assets = {}
    for asset in assets:
        require(isinstance(asset, dict) and isinstance(asset.get("name"), str), "Invalid published asset")
        require(asset["name"] not in published, "Duplicate published asset")
        published[asset["name"]] = asset.get("digest")
        published_assets[asset["name"]] = asset
    for asset_name, digest in expected.items():
        require(isinstance(asset_name, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", asset_name), "Invalid accepted asset name")
        require(isinstance(digest, str) and DIGEST.fullmatch(digest), "Accepted asset digest required")
        require(published.get(asset_name) == digest, "Accepted asset differs from the stable published release")
    artifact = component["artifact"]
    if artifact["kind"] == "package":
        require("sha256:" + artifact["sha256"] in expected.values(), "Accepted component artifact is absent from stable asset provenance")
    else:
        # An OCI manifest digest is not the digest of an exported image tarball.
        # Bind it through a byte-verified attachment from this stable release.
        name = provenance.get("container_provenance_asset")
        require(isinstance(name, str) and name in expected and name in published_assets,
                "Stable container provenance attachment required")
        asset_id = published_assets[name].get("id")
        require(type(asset_id) is int and asset_id > 0, "Invalid container provenance asset ID")
        raw = (fetch_asset or github_asset)(repository, asset_id)
        require(isinstance(raw, bytes) and len(raw) <= 1024 * 1024
                and "sha256:" + hashlib.sha256(raw).hexdigest() == expected[name],
                "Published container provenance bytes differ from the stable asset")
        document = json.loads(raw)
        require(isinstance(document, dict) and document.get("schema_version") == 1
                and document.get("repository") == repository
                and document.get("revision") == revision
                and document.get("version") == component["version"]
                and document.get("artifact") == artifact,
                "Stable container provenance does not bind the accepted image")
    return component["version"]
