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

For the deployment-bundle content contract and offline verification command, see
[deployment bundles](docs/deployment-bundles.md). Registry transport and runnable
Compose/Helm bundles are still being implemented.

Run local checks with `python3 -m unittest discover -s tests -v`.
Python 3.11 or newer is required; the generator has no third-party dependencies.

These templates are starting configurations. Read the deployment checklist before
using them with real traffic. Public documentation does not grant access to
private service implementations.

## License

The documentation, templates and generator in this repository are licensed under
the [Apache License, Version 2.0](LICENSE).

## Template release channels

The declared next bundle version is in [.github/test-channel.json](.github/test-channel.json).
After main template validation succeeds, the test publisher creates
`0.24.0-test.0`, `0.24.0-test.1`, and so on, with the exact tested template archive.
Pull requests validate without publishing. Counters are scoped to this repository
and target version; the publisher never rewrites existing tags or assets.

Choose a gold test by its exact tag. An explicit **Test bundles** workflow dispatch
on main with `gold_test` copies that bundle's assets, including its original test
provenance, unchanged to the declared `0.24.0` stable bundle. It does not rebuild,
change embedded package versions or automatically select a gold candidate.
Stable promotion requires the owner's explicit selection; automated main
publication is confined to the test channel. After stable promotion, declare the
next target before publishing new test candidates.

These are template release bundles, not service package versions or evidence of
runtime/provider acceptance. The services' qualified dependency locks and package
compatibility checks remain separate. See [test bundles](docs/test-bundles.md) for
publication, promotion and retention behavior.

### Additive Python module releases

For complete `packaged_file_sha256` runtime manifests, a minor release may add
ordinary Python modules within an existing package. Every existing file hash and
all other contract fields must remain unchanged. New package initializers, entry
points, native/data files, case collisions, package shadowing and new namespaces
are rejected. Patch releases still require identical contracts. Consumer tests
remain necessary; byte compatibility does not establish live provider behavior.
