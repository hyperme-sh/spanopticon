# Owner images for test deployment

`owner-images/hypermesh-gateway.json` pins a real gateway main build, its successful
owner qualification run, retained artifact ZIP, Docker archive, OCI manifest and
runtime configuration. The verifier checks the API provenance, source labels,
platform and every referenced blob. It never rebuilds an image or executes the
archive. The plane allowlist accepts only the public-core artifact; it excludes
the billing-enabled integration image.

`owner-images/hypermesh-plane.json` pins the separately qualified public-core
plane image, including its OCI index and attestations. It excludes the private
billing integration and preserves all index children unchanged. Its owner run
qualifies source, wheel, rebuilt sdist and the actual core image. The coordinator
verifies this provenance and every byte; it does not claim plane deployment.

On main, `owner-images.yml` copies the verified owner bytes to
`ghcr.io/hyperme-sh/spanopticon-<owner>:0.24.0-test.<run_id>` using the
workflow package token, then downloads and hashes every blob. Existing test tags
must contain identical bytes. No stable tags, Git tags, releases, package visibility
changes or moving aliases are created. PRs only verify. Pins must be refreshed
before the retained owner artifact expires; a missing or expired artifact fails
closed. Publication requires the job token to read the public owner's Actions
artifact and write the destination GHCR package.

The gateway Compose file and Helm chart pin the same manifest digest. They use
port 8000 and `/api/v1/health/`, matching this service rather than the generic
adapter contract. After publication, authenticate Docker/Kubernetes for GHCR as
needed, then run:

```sh
docker compose -f fleet/gateway/compose.yaml up -d --wait
helm upgrade --install gateway-test fleet/gateway/chart --namespace gateway-test --create-namespace --wait
```

These assets exercise startup with the owner's empty-ticket stand-in. They disable
WireGuard and background assignment refresh, force live API-key validation, and
provide no provider or tenant credentials. Health readiness is **not** ticket,
authentication, routing, assurance or real fleet acceptance. They are not an
operational gateway configuration. Do not expose them through a public ingress.

There is deliberately no `test.json` admission inventory yet. The core plane deployment assets,
dependent service images and actual assurance adapter configuration
must be qualified together in Compose and Helm before the separate fleet bundle
publisher can advance its `test` channel. Image publication alone cannot satisfy
that gate. Chart version `0.0.0` is a source placeholder, not a release.

See [qualification input requirements](qualification-inputs.md) for the separate bundle inventory and credential contract.
