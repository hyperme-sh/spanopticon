# Spanopticon

Documentation and deployment templates for independently owned service planes.
A plane combines services behind explicit identity, tenant, event and operations
contracts. Service implementations and their release cycles remain in their own
repositories.

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
private service implementations. Licensing has not yet been selected.
