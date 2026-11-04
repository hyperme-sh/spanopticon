# Spanopticon

Documentation and deployment templates for independently owned service planes.
A plane combines services behind explicit identity, tenant, event and operations
contracts. Service implementations and their release cycles remain in their own
repositories.

See [coordinated releases](docs/releases.md) for shared major/minor versions,
independent patches and the compatibility gate.

Start with [architecture](docs/architecture.md), then
[generate a deployment](docs/deployment.md). The generator produces Docker Compose
and Kubernetes manifests for an image you supply by immutable SHA-256 digest.
It creates files only; it does not deploy resources, provision credentials or
publish service images.

```sh
python3 scripts/new_plane.py example-plane \
  --image registry.example.org/team/plane@sha256:YOUR_64_HEX_DIGEST \
  --output ./example-plane
```

Replace the example image and digest with a qualified application artifact.
The image must implement the [runtime contract](docs/runtime-contract.md).

Run local checks with `python3 -m unittest discover -s tests -v`.
Python 3.11 or newer is required; the generator has no third-party dependencies.

These templates are starting configurations. Read the deployment checklist before
using them with real traffic. Public documentation does not grant access to
private service implementations.

## License

The documentation, templates and generator in this repository are licensed under
the [Apache License, Version 2.0](LICENSE).

## Template release channels

A successful main push publishes the tested template/generator archive as an
immutable `test-<commit>` GitHub prerelease, after both template checks and the
coordinated-release policy gate pass. A `vMAJOR.MINOR.PATCH` tag at that commit
promotes the same archive bytes to a stable release. This is a template release,
not publication or deployment of the services described by a plane manifest.
The coordinated service compatibility policy remains a separate required check.

Repository immutable releases must be enabled. Promotion verifies the original
successful Actions artifact, not just release metadata, and must occur before
that artifact expires (90-day requested retention). Expired evidence requires a
new validated main revision. No deployment credentials or reviewer gate are used.
