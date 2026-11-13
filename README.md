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
  --repository registry.example.org/team/plane --image registry.example.org/team/plane:test \
  --assurance-config examples/assurance/kyc.json \
  --output ./example-plane
```

Replace the example image and digest with a qualified application artifact.
The image must implement the [runtime contract](docs/runtime-contract.md).

For digest-pinned bundle verification, see [deployment bundles](docs/deployment-bundles.md).
[Fetch and deploy a test bundle](docs/test-deployment.md) covers GHCR test-channel
resolution, local assembly and validated Compose/Helm plan/apply commands.
Install `requirements-deployment.txt` before running deployment tools or the full
test suite; the generator, fetcher and content verifier remain standard-library-only.

Run local checks with `python3 -m unittest discover -s tests -v`.
Python 3.11 or newer is required; the generator has no third-party dependencies.

These templates are starting configurations. Read the deployment checklist before
using them with real traffic. Public documentation does not grant access to
private service implementations.

## License

The documentation, templates and generator in this repository are licensed under
the [Apache License, Version 2.0](LICENSE).

## Template release channels

Main template validation remains. The former Test bundles workflow that
published `0.24.0-test.N` GitHub prereleases and promoted a gold candidate has
been removed. Automatic package or template prereleases are not published.
Fleet test container tags are documented in [docs/fleet-test-publication.md](docs/fleet-test-publication.md).
See [test bundles](docs/test-bundles.md) for the current policy.

### Additive Python module releases

For complete `packaged_file_sha256` runtime manifests, a minor release may add
ordinary Python modules within an existing package. Every existing file hash and
all other contract fields must remain unchanged. New package initializers, entry
points, native/data files, case collisions, package shadowing and new namespaces
are rejected. Patch releases still require identical contracts. Consumer tests
remain necessary; byte compatibility does not establish live provider behavior.

The [minimum bundle](profiles/minimum/README.md) includes a tenant-scoped reference API, Python client, local test issuer, and Compose and Helm installation instructions.
