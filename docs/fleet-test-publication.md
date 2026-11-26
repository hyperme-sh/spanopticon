# Publish a qualified fleet test bundle

The test publisher copies an already-qualified OCI layout to
`ghcr.io/hyperme-sh/spanopticon-fleet-test`, verifies it by downloading every
blob, and only then advances `test`. It does not build images, rewrite embedded
versions, create Git tags/GitHub releases, or accept stable destinations.

Fleet test autotagging/publication has standing owner authorization. No further
human approval is needed for PR-based test updates. Automated qualification still
applies. Stable releases and gold promotion remain separately authorized work.

## Qualification producer contract

The reusable `.github/workflows/publish-fleet-test.yml` is restricted to a main push or scheduled reconciliation on
Spanopticon main from `.github/workflows/qualify-fleet.yml`. The producer now validates reviewed `fleet/` inputs, assembles one OCI bundle,
exercises the exact Compose and Helm bytes including Helm upgrade, and uploads
the same-run/attempt artifact before calling the publisher through `needs: qualify`.
The generated Portal inventory is `fleet/services.json`. It follows the Portal
owner's qualified `runtime:test` channel. The fleet workflow reconciles it on
main updates and on its regular schedule, resolves that owner channel once, and
reads the package version and source revision from the exact selected image.
If the published fleet already contains that build, the scheduled check stops.
Owner image qualification stays in the Portal release workflow.

## Generate and validate Portal assets

Edit `fleet/services.json` to select the supported owner channel. Adding another
component requires a renderer for its runtime and qualification requirements.
Retiring this enrollment means removing Portal from the generated lane or
replacing that lane with a reviewed inventory for the remaining components.

The resolver consumes the owner image and generates the whole input set. The
offline generator also accepts an already verified owner record with
`selector`, `image`, `version`, and `source_revision`:

```sh
python3 scripts/render_fleet.py --qualified /path/to/owner-release.json \
  --candidate 0.24.0-test.123 --output /new/path/portal-fleet
```

The output contains `test.json`, `compose.json`, and `chart.tgz`. Use the same
inputs and candidate to reproduce the bytes. Both formats use the existing
Portal owner image and require runtime configuration for the service: the
Portal origin, Keycloak issuer and client, Edge URL, active tenant UUID, tenant
picker setting and routes, and tenant version map. Compose loads those settings
as environment variables and reads the session secret from the file named by
`PORTAL_AUTH_SECRET_FILE` through the mounted `portal-auth` secret. Helm reads
those settings from the existing `portal-settings` ConfigMap and reads the
session secret from key `token` in the `portal-auth` Secret. Its image pull uses
the `fleet-ghcr` registry Secret. Generate or provision real values through the
owning environment configuration. The assets contain secret names and file
references.

Qualification uses disposable credentials and project-controlled signed-out
settings. It prepares a job-local auth secret, a scoped private GHCR pull
Secret, and configuration for the isolated Helm namespace automatically.
The workflow reads `FLEET_PORTAL_GHCR_PULL_USER` and
`FLEET_PORTAL_GHCR_PULL_TOKEN` from the existing repository config and secret
store. If either is unavailable it reports that the Portal lane is blocked and
skips publication. Keep the pull identity scoped to the Portal owner package.
The publisher uses its existing package writer. The portal-only bundle uses
external TEST services. An inventory containing an assurance service still
requires the full assurance profile and configured provider credential references.

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
  if: github.ref == 'refs/heads/main' && needs.qualify.outputs.ready == 'true'
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

## Fleet qualification execution

See [fleet inputs](../fleet/README.md) for the reviewed input contract. PR validation
checks the component/asset inventory and Helm render without accessing credentials
or applying workloads. Only main pushes run qualification with test-only assurance
credentials, a disposable kind context and the job Docker daemon. Success records
are written only after both runtime consumers, Helm upgrade and cleanup succeed.
Cleanup removes the task-owned namespace and Compose resources without deleting
application volumes. Credentials and expanded runtime files stay outside the
uploaded OCI artifact. A failure leaves no successful qualification record.

A `portal` component uses the Portal owner's qualified image from
`ghcr.io/hyperme-sh/controlplane-portal/runtime`. The main qualification workflow
reads the running Portal's signed-out footer in both engines and after Helm
upgrade. The footer must show the qualified image version and build and the exact
candidate tag resolved for that deployment. Configure the Portal workload to
serve its signed-out page on port 8080 in the disposable qualification job.

This initial producer establishes readiness and upgrade of the selected assets.
Owner build provenance, dependency compatibility, inter-service/business smoke,
private image access and real provider behavior still need their own evidence.
The workflow does not infer those outcomes from container health.
