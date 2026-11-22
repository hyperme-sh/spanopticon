"""Admit a qualified owner OCI patch against the most recent immutable stable API."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release.policy import PolicyError, compatible, require, version


OWNERS = {
    "hyperme-sh/acl-service": "acl_service",
    "hyperme-sh/auth-service": "auth_service",
    "hyperme-sh/user-service": "user_service",
}
DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")
SHA = re.compile(r"[a-f0-9]{40}\Z")


def github(path):
    return json.loads(subprocess.check_output(["gh", "api", path], timeout=40))


def previous_release(repository, target, fetch=github):
    wanted = version(target.removeprefix("v"))
    matches = []
    for page in range(1, 101):
        releases = fetch(f"repos/{repository}/releases?per_page=100&page={page}")
        require(isinstance(releases, list) and len(releases) <= 100, "Incomplete release inventory")
        for release in releases:
            if release.get("prerelease") is not False or release.get("draft") is not False:
                continue
            tag = release.get("tag_name", "")
            if not re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", tag):
                continue
            number = version(tag[1:])
            if number[:2] == wanted[:2] and number != wanted:
                matches.append((number, release))
        if len(releases) < 100:
            break
    else:
        raise PolicyError("Release inventory exceeds the verification limit")
    require(matches, "A prior stable release on the same line is required")
    number, latest = max(matches, key=lambda item: item[0])
    require(number[2] < wanted[2], "Patch is older than the current stable release")
    require(latest.get("immutable") is True, "Prior stable release must be immutable")
    return latest


def baseline_image(repository, tag):
    release = previous_release(repository, tag)
    previous = release["tag_name"]
    asset, = (asset for asset in release["assets"] if asset.get("name") == "runtime.json")
    source = github(f"repos/{repository}/commits/{previous}")["sha"]
    require(isinstance(source, str) and SHA.fullmatch(source), "Missing stable tag revision")
    with tempfile.TemporaryDirectory(prefix="owner-runtime-baseline-") as directory:
        subprocess.run(["gh", "release", "download", previous, "--repo", repository,
                        "--pattern", "runtime.json", "--dir", directory], check=True, timeout=60)
        path = Path(directory) / "runtime.json"
        require(path.stat().st_size <= 65536, "Stable runtime metadata is oversized")
        raw = path.read_bytes()
    require("sha256:" + hashlib.sha256(raw).hexdigest() == asset.get("digest"),
            "Stable runtime metadata differs from the immutable release asset")
    image = f"ghcr.io/{repository}/runtime"
    doc = json.loads(raw)
    require(type(doc.get("schema_version")) is int and doc["schema_version"] == 1
            and doc.get("version") == previous and doc.get("repository") == image and doc.get("revision") == source
            and isinstance(doc.get("digest"), str) and DIGEST.fullmatch(doc["digest"]),
            "Stable runtime metadata does not bind the prior source and OCI image")
    return image + "@" + doc["digest"], source


def openapi(image, repository, revision, *, run=subprocess.run):
    run(["docker", "pull", image], check=True, capture_output=True, timeout=180)
    inspect = run(["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", image],
                  check=True, capture_output=True, text=True, timeout=30)
    labels = json.loads(inspect.stdout)
    source = labels.get("org.opencontainers.image.source")
    source_matches = source == f"https://github.com/{repository}"
    # Retained User images carry the revision but predate a source URL label.
    source_matches |= repository == "hyperme-sh/user-service" and source is None
    require(source_matches and labels.get("org.opencontainers.image.revision") == revision,
            "The OCI image does not identify the expected source revision")
    code = f"import json; from {OWNERS[repository]}.app import app; print(json.dumps(app.openapi()))"
    exported = run(["docker", "run", "--rm", "--network", "none", "--read-only",
                    "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "--entrypoint", "python",
                    image, "-I", "-B", "-c", code], check=True, capture_output=True, text=True, timeout=60)
    require(len(exported.stdout) <= 4 * 1024 * 1024, "Runtime API document is oversized")
    return json.loads(exported.stdout)


def compare(previous, candidate):
    for document in (previous, candidate):
        require(isinstance(document, dict) and isinstance(document.get("openapi"), str)
                and isinstance(document.get("paths"), dict) and document["paths"]
                and isinstance(document.get("info"), dict), "Complete OpenAPI export required")
        document["info"].pop("version", None)
    new_paths = candidate["paths"].keys() - previous["paths"].keys()
    require(all(path.startswith("/api/v1/internal/") or path == "/auth/startup/identity/nightly-member"
                for path in new_paths), "The patch adds a public API operation")
    require(compatible(previous, candidate), "The patch changes an existing OpenAPI operation or schema")


def main(repository, tag, revision, digest):
    require(repository in OWNERS, "Unexpected OCI owner repository")
    require(re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", tag)
            and SHA.fullmatch(revision) and DIGEST.fullmatch(digest), "Exact patch tag, source and OCI digest required")
    require(github(f"repos/{repository}/commits/{tag}")["sha"] == revision,
            "Candidate OCI source differs from the exact patch tag")
    old_image, old_source = baseline_image(repository, tag)
    new_image = f"ghcr.io/{repository}/runtime@{digest}"
    compare(openapi(old_image, repository, old_source), openapi(new_image, repository, revision))
    print(f"{repository} {tag} preserves the existing OpenAPI contract of its immutable stable runtime.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository")
    parser.add_argument("tag")
    parser.add_argument("revision")
    parser.add_argument("digest")
    try:
        main(**vars(parser.parse_args()))
    except (PolicyError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        detail = str(error) if isinstance(error, PolicyError) else type(error).__name__
        sys.exit("Owner patch admission refused. " + detail)
