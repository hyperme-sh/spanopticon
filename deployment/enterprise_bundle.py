"""Package the named Compose and Helm configurations for the TEST registry."""

import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile

from deployment import bundle as b
from deployment.publish import Registry, run
from scripts.service_channels import reference

PROFILES = ("s3rch", "hyperme", "spanopticon")
ARTIFACT_TYPE = "application/vnd.spanopticon.enterprise-config.v1"
METADATA_TYPE = ARTIFACT_TYPE + "+json"
ARCHIVE_TYPE = ARTIFACT_TYPE + ".tar+gzip"
CANDIDATE = r"0\.24\.0-test\.[1-9][0-9]*"
WORKFLOW = "hyperme-sh/spanopticon/.github/workflows/enterprise-config-bundles.yml@refs/heads/main"


def repository(profile):
    b.require(profile in PROFILES, "Unknown enterprise configuration")
    return f"ghcr.io/hyperme-sh/spanopticon-config-{profile}"


def digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def package(root, profile, candidate, revision, output):
    import yaml

    repo = repository(profile)
    b.require(b.matches(CANDIDATE, candidate) and b.matches(r"[a-f0-9]{40}", revision),
              "Expected a TEST candidate and source commit")
    root, output = Path(root), Path(output)
    files = ["README.md", f"compose.{profile}.yaml", "helm/Chart.yaml", "helm/values.yaml",
             f"helm/values.{profile}.yaml", "helm/templates/workloads.yaml"]
    entries = {name: (root / name).read_bytes() for name in files}
    values = yaml.safe_load(entries[f"helm/values.{profile}.yaml"])
    images = {name: reference(row["image"], row["image"].rsplit(":", 1)[0])
              for name, row in values["services"].items()}
    stream = io.BytesIO()
    with gzip.GzipFile(fileobj=stream, mode="wb", mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w") as archive:
            for name, raw in sorted(entries.items()):
                item = tarfile.TarInfo(name)
                item.size = len(raw)
                item.mode = 0o644
                archive.addfile(item, io.BytesIO(raw))

    output.mkdir(mode=0o700)
    blobs = output / "blobs/sha256"
    blobs.mkdir(parents=True)

    def store(raw, media_type, name=None):
        descriptor = {"mediaType": media_type, "digest": digest(raw), "size": len(raw)}
        (blobs / descriptor["digest"].split(":")[1]).write_bytes(raw)
        if name:
            descriptor["annotations"] = {"org.opencontainers.image.title": name}
        return descriptor

    metadata = {"profile": profile, "candidate": candidate, "source_revision": revision, "images": images}
    manifest = {
        "schemaVersion": 2,
        "mediaType": b.OCI,
        "artifactType": ARTIFACT_TYPE,
        "annotations": {"org.opencontainers.image.source": "https://github.com/hyperme-sh/spanopticon"},
        "config": store(b"{}", b.EMPTY),
        "layers": [store(encoded(metadata), METADATA_TYPE, "bundle.json"),
                   store(stream.getvalue(), ARCHIVE_TYPE, f"{profile}.tar.gz")],
    }
    descriptor = store(encoded(manifest), b.OCI)
    (output / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n')
    (output / "index.json").write_bytes(encoded({"schemaVersion": 2, "manifests": [descriptor]}))
    return f"{repo}@{descriptor['digest']}"


def resolve(profile, selector, client):
    repo = repository(profile)
    b.require(client.repository == repo and (selector == "test" or b.matches(CANDIDATE, selector)),
              "Expected the configuration TEST channel or exact candidate")
    manifest, headers = client.get("manifests", selector, b.MAX_METADATA)
    manifest_digest = digest(manifest)
    b.require(manifest_digest == headers.get("Docker-Content-Digest"), "Configuration manifest digest differs")
    manifest = b.document(manifest)
    b.require(manifest.get("mediaType") == b.OCI and manifest.get("artifactType") == ARTIFACT_TYPE
              and manifest.get("schemaVersion") == 2,
              "The registry artifact is not an enterprise configuration")
    layers = manifest["layers"]
    b.require([row["mediaType"] for row in layers] == [METADATA_TYPE, ARCHIVE_TYPE],
              "Unexpected configuration layers")
    config = manifest["config"]
    b.descriptor(config, b.EMPTY)
    for descriptor, name in zip(layers, ("bundle.json", f"{profile}.tar.gz"), strict=True):
        b.require(descriptor.get("annotations") == {"org.opencontainers.image.title": name},
                  "Unexpected configuration filename")
        b.descriptor({key: value for key, value in descriptor.items() if key != "annotations"},
                     descriptor["mediaType"])

    def fetch(descriptor):
        raw, _ = client.get("blobs", descriptor["digest"], b.MAX_METADATA)
        b.require(len(raw) == descriptor["size"] and digest(raw) == descriptor["digest"],
                  "Configuration blob digest or size differs")
        return raw

    b.require(fetch(config) == b"{}", "Expected an empty OCI image configuration")
    metadata = b.document(fetch(layers[0]))
    fetch(layers[1])
    b.keys(metadata, ("profile", "candidate", "source_revision", "images"), "configuration metadata")
    b.require(metadata["profile"] == profile and b.matches(CANDIDATE, metadata["candidate"])
              and (selector == "test" or metadata["candidate"] == selector)
              and b.matches(r"[a-f0-9]{40}", metadata["source_revision"]),
              "Configuration package identity differs")
    b.require(isinstance(metadata["images"], dict) and metadata["images"], "Expected service tag references")
    for image in metadata["images"].values():
        b.require(isinstance(image, str), "Expected a service image tag")
        reference(image, image.rsplit(":", 1)[0])
    return {**metadata, "bundle_reference": f"{repo}@{manifest_digest}"}


def publish(root, env=None):
    env = os.environ if env is None else env
    b.require(env.get("GITHUB_REPOSITORY") == "hyperme-sh/spanopticon"
              and env.get("GITHUB_EVENT_NAME") == "push"
              and env.get("GITHUB_REF") == "refs/heads/main"
              and env.get("GITHUB_WORKFLOW_REF") == WORKFLOW,
              "Only the main enterprise configuration workflow may publish")
    revision, run_id = env["GITHUB_SHA"], env["GITHUB_RUN_ID"]
    candidate = f"0.24.0-test.{run_id}"
    user, token = env["GHCR_USERNAME"], env["GHCR_TOKEN"]
    records = {}
    with tempfile.TemporaryDirectory(prefix="enterprise-config-publish-") as scratch:
        scratch = Path(scratch)
        auth = str(scratch / "auth.json")
        run(["oras", "login", "ghcr.io", "--registry-config", auth, "--username", user, "--password-stdin"],
            input_bytes=token.encode())
        for profile in PROFILES:
            repo = repository(profile)
            layout = scratch / profile
            expected = package(root, profile, candidate, revision, layout)
            client = Registry(repo, user, token)
            previous = client.optional_manifest("test")
            advance = True
            if previous:
                current = resolve(profile, "test", client)
                b.require(current["bundle_reference"] == f"{repo}@{previous}", "Moving configuration tag changed")
                prior_id = int(current["candidate"].rsplit(".", 1)[1])
                advance = prior_id <= int(run_id)
                b.require(prior_id != int(run_id) or current["bundle_reference"] == expected,
                          "Existing candidate uses different configuration")
            existing = client.optional_manifest(candidate)
            b.require(existing is None or expected == f"{repo}@{existing}", "Immutable candidate has different bytes")
            if existing is None:
                run(["oras", "cp", "--from-oci-layout", f"{layout}@{expected.split('@')[1]}",
                     f"{repo}:{candidate}", "--to-registry-config", auth])
            selected = resolve(profile, candidate, client)
            b.require(selected["bundle_reference"] == expected, "Published configuration differs from local bytes")
            if advance:
                b.require(client.optional_manifest("test") == previous, "Moving configuration tag changed")
                if expected != f"{repo}@{previous}":
                    run(["oras", "tag", expected, "test", "--registry-config", auth])
                b.require(resolve(profile, "test", client) == selected, "Moving configuration tag differs")
            records[profile] = selected
    return records
