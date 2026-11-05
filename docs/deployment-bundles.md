# Deployment bundle content and locks

A deployment bundle binds one candidate's component image references to its
Compose and Helm artifacts. It is separate from the template archive published
by the current test-bundle workflow. The initial implementation verifies local
OCI blobs and writes a lock; GHCR publication/resolution, chart generation,
installation and runtime qualification are tracked in [#20](https://github.com/hyperme-sh/spanopticon/issues/20).

## Verify downloaded content

```sh
python3 scripts/lock_deployment.py \
  --layout ./downloaded-bundle \
  --reference ghcr.io/OWNER/BUNDLE@sha256:EXPECTED_MANIFEST_DIGEST \
  --output ./deployment.lock.json
```

Replace the uppercase placeholders with a lowercase repository and actual digest.
The directory contains `blobs/sha256/<hex-digest>` files, as in an OCI image layout.
The expected manifest digest must come from a trusted, separately resolved bundle
selection. This command neither resolves `test` nor fetches from GHCR. It does not
read the layout index or trust a tag stored in it. A future resolver must resolve
the moving test selector once and fetch that manifest and all referenced blobs by
digest, then pass the exact reference here.

The verifier checks the raw manifest hash, supported media types, config bytes,
all layer sizes and hashes, and agreement between the bundle metadata and actual
Compose/Helm layer descriptors. It refuses malformed metadata, duplicate JSON
keys, floating component image tags, unexpected layers, symlink blob paths and
oversized content. Output creation is exclusive; existing files and symlinks are
not replaced. Validation failures do not create a lock. I/O failures during the
final write may leave a partial file for inspection.

## Supported OCI profile

The profile follows the [OCI image manifest](https://github.com/opencontainers/image-spec/blob/main/manifest.md)
and [content descriptor](https://github.com/opencontainers/image-spec/blob/main/descriptor.md)
structures, with a deliberately narrow supported field set:

- `schemaVersion: 2`, `mediaType: application/vnd.oci.image.manifest.v1+json`.
- `artifactType: application/vnd.spanopticon.deployment.bundle.v1+json`.
- `config` is a descriptor for exactly `{}` using `application/vnd.oci.empty.v1+json`.
- `layers` has exactly one metadata layer and one layer for each deployment asset
  listed below, in any order. Each descriptor has exactly `mediaType`, `digest`
  (SHA-256) and `size` (positive integer). External URLs, embedded data, annotations,
  subjects and unknown fields are not supported by this initial profile.

Metadata is a JSON object with exactly these fields:

| Field | Meaning |
| --- | --- |
| `schema` | Integer `1` |
| `candidate` | Original `MAJOR.MINOR.PATCH-test.N` identity, preserved on promotion |
| `source_revision` | Full 40-character lowercase source commit |
| `components` | Nonempty mapping of DNS-label names to `image`, `version`, `source_revision` |
| `deployment_assets` | Exactly `compose` and `helm` descriptors matching the corresponding OCI layers |

Components use `ghcr.io/owner/image@sha256:<digest>`, including when the digest
identifies a multi-platform index. Embedded package versions remain unchanged;
they need not equal the bundle label. The component revision and version are
producer declarations, not independently verified source/build provenance.

Layer media types:

| Layer | Media type |
| --- | --- |
| Metadata | `application/vnd.spanopticon.deployment.bundle.v1+json` |
| Compose | `application/vnd.spanopticon.compose.v1+json` |
| Helm chart | `application/vnd.cncf.helm.chart.content.v1.tar+gzip` |

Metadata is limited to 1 MiB, layers total 256 MiB, and components to 128.
Deployment asset bytes are hashed without extraction or execution. The verifier
does not parse Compose semantics or inspect a Helm archive: a matching digest is
content integrity, not a runnable or safe deployment. Component images are pinned
but are not downloaded or verified by this command.

## Test and production selection

The lock records the immutable GHCR bundle reference, metadata digest, candidate,
component declarations and deployment asset descriptors. Its verification marker
is `local-content-only`. It deliberately has no production-admission flag and
makes no compatibility, availability, signature or health claim.

A promoted gold bundle preserves the original test candidate and all payload
bytes. Production channel authorization and release provenance must be established
by a separate trusted selector; a user-supplied hash or lock cannot establish
that promotion occurred. The production deployment path must reject an unpromoted
test selection, never fall back to the test channel, and verify its authorization
before executing any deployment. No production selector exists in this change.

Next steps are authenticated GHCR transport and channel selection, bundle assembly,
Compose/Helm consumers, dependency/migration validation, disposable deployment
smoke tests, rollback and observed runtime receipts. The verifier must not be
used as a substitute for those checks.
