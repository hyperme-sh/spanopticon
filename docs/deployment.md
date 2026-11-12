# Generate and qualify a deployment

Use `scripts/new_plane.py NAME --repository REGISTRY/REPOSITORY --image REGISTRY/REPOSITORY:TAG --assurance-config CONFIG.json --output DIRECTORY`.
Names are lowercase DNS labels up to 63 characters and must start with a letter,
so Service names work without optional cluster feature gates. Images require an explicit tag in the configured registry/repository. Runtime digests are refused.
The output directory must not exist; existing deployments are never overwritten.
Both output files use JSON, which Docker Compose and Kubernetes accept directly.

## Local Compose

Inspect `compose.yaml`, configure required service settings through a separate
Compose override and validate the result:

```sh
SPANOPTICON_SECRET_KYC_CLIENT_FILE=/secure/kyc-client \
  docker compose -f example-plane/compose.yaml config --quiet
```

The default port binds only to loopback (`127.0.0.1:8080`). Starting the workload
is an explicit operator action. Choose a different host port in an override if
needed. The image is private only if its registry enforces that; configure
registry login separately. Compose does not supply HTTP readiness probes because
minimal images may contain neither a shell nor an HTTP client. Verify both health
endpoints externally before sending traffic.

## Kubernetes

`kubernetes.json` contains a Namespace, Deployment, ClusterIP Service and
default-deny NetworkPolicy scoped to the generated application pods. It creates no public ingress,
credentials, storage, workload identity or cloud resources. The pod disables
automatic service-account token mounting, drops capabilities and uses the runtime
default seccomp profile.

Before applying, add narrowly scoped network policies for authorized callers,
DNS and required dependencies, plus reviewed credential/image-pull-secret and
configuration overlays. Use a network-policy-enforcing CNI. Validate with your
cluster version's schema and admission policies. Default-deny means the generated
service cannot receive ordinary application traffic until you authorize it.

Run server-side dry-run against the intended cluster only after reviewing the
context and namespace. Verify that the application name and labels do not collide
with an existing workload; use a dedicated namespace for each plane. Application readiness, migration/rollback compatibility,
resource sizing, backup restore and dependency outage behavior require integration
qualification. The example limits and probe timings are initial values, not
capacity claims.

## Release record

Record the image digest, configuration revision, dependency contracts, migration
plan, checks performed, deployment environment and rollback owner. Separate
local validation, CI results, deployment and observed runtime acceptance. Never
record credentials or private customer data in public release documentation.

## Required assurance selection

Use `examples/assurance/kyc.json` as a configuration shape, replacing its
`.invalid` URL and provisioning the external credential through your secret
manager. It contains no usable credential. Supported modes are `kyc`,
`verification` and `both`. The `providers` object must contain exactly the selected
keys (`kyc` and/or `verification`), each with `url` and `credential_secret`.
`render_release.py` also requires `--assurance-config`; neither rendering entry
point permits a deployment with neither authority selected.

The generic renderer applies the contract to each rendered plane. Only render
images implementing that contract, and use credentials scoped to their owner
interfaces. This is not a credential distribution mechanism for arbitrary
microservices. See [runtime contract](runtime-contract.md#deployment-assurance)
for availability, evidence, and independent Stripe payment requirements.

## Independent service channels

The registry/repository is trusted deployment configuration, not derived from
the supplied image. The shared contract is vendored from `.github` commit
`a1963cff270b3336348061ae6efe638bebdea876`; source hashes and positive/negative
vectors accompany it. The CLI requires `--repository` and keeps the tag unchanged.
Compose pulls on each start; Kubernetes pulls on each pod start. Existing pods
need the deployment owner's normal channel reconciliation to advance.

`render_release.py` accepts `--services service-channels.json` and
`--repositories approved-repositories.json`. The service map uses schema 1 and a
`services` object; the repository file maps each service name to its allowed
registry/repository. Every container component must be present. Compatible peers
may use different patch tags. Artifact identity stays in the independently
validated release.json and does not replace the running service reference.

The explicit `--artifact-qualification-only` path preserves historical isolated
bundle qualification under #20. It is not the running TEST configuration path.
Production still requires its existing explicit promotion; supplying a tag here
does not authorize it.

Runtime and minimum-profile checks are registered in Infra's `spanopticon-runtime`
nightly adapter (infra #922). PRs run unit/contract and Helm rendering checks only.
