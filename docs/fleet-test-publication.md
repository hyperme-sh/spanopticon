# Publish a qualified fleet test bundle

The test publisher copies an already-qualified OCI layout to
`ghcr.io/hyperme-sh/spanopticon-fleet-test`, verifies it by downloading every
blob, and only then advances `test`. It does not build images, rewrite embedded
versions, create Git tags/GitHub releases, or accept stable destinations.

Fleet test autotagging/publication has standing owner authorization. No further
human approval is needed for PR-based test updates. Automated qualification still
applies. Stable releases and gold promotion remain separately authorized work.

## Qualification producer contract

The reusable `.github/workflows/publish-fleet-test.yml` is restricted to a push to
Spanopticon main from `.github/workflows/qualify-fleet.yml`. That producer is the
remaining integration point: it must assemble the actual owner-image inventory,
qualify dependencies and provenance, exercise the exact Compose and Helm bundle,
and upload its output before calling the publisher through `needs: qualify`.
This change does not add a substitute fixture producer or claim a fleet has been
published. The existing template archive workflow remains separate.

The producer must use the candidate `0.24.0-test.${GITHUB_RUN_ID}`. GitHub allocates
that identity before qualification, so the chart/bundle can be tested without
rewriting bytes afterward. All participating images are selected by digest in
one bundle; embedded package versions remain unchanged. This counter is scoped
to the fleet GHCR package, not the independent template-archive Git tag counter.
Re-running a workflow keeps the candidate: changed bytes are refused, and require
a new qualifying main run. Do not advance `next` as part of this flow.

Upload one artifact named `fleet-test-bundle-${GITHUB_RUN_ATTEMPT}` containing:

- `oci/`: the complete layout from `assemble_test_bundle.py`, including `index.json`,
  `oci-layout`, and verified blobs.
- `qualification.json`: a record with exactly `schema`, `source_revision`,
  `run_id`, `run_attempt`, `bundle_reference`, and `receipts`.

The schema is `spanopticon-fleet-qualification/v1`. Run ID and attempt are decimal
strings. The source revision is the exact main commit being qualified. The bundle
reference uses the fixed package above and its immutable manifest digest.
`receipts` contains the original `compose` and `helm` apply receipt objects from
`deploy_test_bundle.py`; both must report `tool-wait-succeeded` for that exact
bundle, candidate, image inventory and assurance configuration. Plan-only receipts,
missing observations, different source/run identities and malformed bytes fail
before registry login. Readiness receipts are not provider/business acceptance.

The producer is responsible for runtime smoke, source/image provenance and
service-specific compatibility beyond these receipt bindings. A fabricated JSON
receipt is not independent attestation; trusted reviewed qualification code is
the producer boundary. The publisher is deliberately unavailable to PR runs,
arbitrary repositories, dispatch events or unrelated workflows.

After a successful qualification job, the producer calls:

```yaml
publish:
  needs: qualify
  if: github.event_name == 'push' && github.ref == 'refs/heads/main'
  permissions:
    contents: read
    actions: read
    packages: write
  uses: ./.github/workflows/publish-fleet-test.yml
```

Only this publishing job receives package-write permission. Qualification jobs
should retain read-only permissions. The reusable workflow downloads the artifact
from its own run/attempt; it does not accept a user-supplied repository, run ID,
artifact name or destination. Public Spanopticon uses hosted runners; private
owner workflows retain their debrunner routing.

## Publication ordering and retry

1. Validate source/run identity, all local blobs, and both runtime receipts.
2. Inspect the immutable candidate. If it already exists, require the same digest.
   Authentication/transport errors are never treated as an absent manifest.
3. Verify any existing `test` bundle and its counter.
4. Log in with the job token using stdin and a temporary private registry config.
5. Copy the exact OCI manifest and blobs with [ORAS copy](https://oras.land/docs/commands/oras_cp/).
6. Fetch the candidate through the independent GHCR reader and verify every blob.
7. Recheck the channel, then [tag the verified digest](https://oras.land/docs/commands/oras_tag/)
   as `test` only when its counter does not move backwards. Fetch and verify it again.
8. Retain a publication receipt with the source, candidate, digest and alias result.

All publishers for this package must use the shared
`spanopticon-fleet-test-publication` concurrency group. OCI tag writes do not
provide compare-and-swap or registry-enforced immutability: the existence and
channel checks protect this serialized publisher, not concurrent external writers.
Do not publish this package through another workflow/tool. An unexpected channel
change fails rather than silently overwriting it. A failed alias update may leave
a valid exact candidate; a retry reuses identical bytes. No cleanup deletes tags,
releases, images or application volumes.

Credentials are never command-line values, receipts or child diagnostics. The
publisher removes its temporary auth directory on exit. The job token needs GHCR
package write access; public anonymous fetch and private access still need live
qualification after the first actual publication. No package visibility changes
are made here.
