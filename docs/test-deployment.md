# Fetch and deploy a test bundle

These commands consume the [deployment bundle profile](deployment-bundles.md).
They support test selection, Compose plans/apply, and Helm plans/install/upgrade.
They do not publish OCI artifacts, select production, promote releases or change
package versions. The existing GitHub template archives are not OCI deployment
bundles: a producer must first assemble and publish the profile described below.
The [fleet test publisher](fleet-test-publication.md) consumes qualified main-run
artifacts; the actual fleet qualification producer remains to be integrated.

## Prerequisites

Use Python 3.11+, Docker Compose with `up --wait` support, and Helm 3 plus kubectl
for Kubernetes deployment. Native CI qualifies Helm 3.22.1 and a disposable kind
cluster. Install the parser in an isolated environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-deployment.txt
```

The fetcher and offline lock verifier need only Python's standard library.
Deployment uses the pinned PyYAML parser for rendered Helm manifests.

Choose a trusted GHCR repository. Public content uses anonymous pull tokens. For
private bundle content, supply `GHCR_USERNAME` and `GHCR_TOKEN` through your secret
manager; neither is accepted as a command-line argument or written to receipts.
The token needs read access to that package. GitHub documents the available
[Container registry authentication](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).
These credentials are for the Python fetcher only: Docker credential helpers and
Kubernetes `imagePullSecrets` independently handle component image pulls.

## Resolve the test channel once

Replace the example repository with your actual deployment bundle repository:

```sh
python scripts/fetch_test_bundle.py \
  --repository ghcr.io/your-org/your-plane \
  --selector test --next 0.24.0 --output ./candidate
```

The selector is either `test` or an exact `0.24.0-test.N`. Stable versions, `prod`
and `latest` are rejected. The fetcher retrieves the selected manifest once,
checks its digest header against the received bytes, and fetches every remaining
blob by digest. It verifies the complete bundle before writing `selection.json`.
The candidate must belong to the requested next version and, for an exact tag,
match that tag. A channel move during download cannot mix candidate contents.

Only GHCR's fixed token endpoint receives credentials. Blob redirects are limited
to the GHCR storage host and carry no authorization header. Requests have size and
time limits. Existing output paths are refused. Failed downloads may leave partial
blobs but have no completed selection receipt and cannot be deployed.

The resulting selection is test-channel transport evidence, not a signature,
source-build attestation, compatibility proof or production admission. Protect
local bundle/plan directories as operator-owned inputs. Consumers rehash every
asset and rederive the lock before use; editing a cached lock does not change the
selected images. This does not make a malicious repository trustworthy.

## Compose plan and apply

Select your actual test Docker context and provision the credential file required
by the assurance configuration. For `examples/assurance/stripe-kyc.json`, that
means setting `SPANOPTICON_SECRET_STRIPE_KYC_CLIENT_FILE` to the operator-owned
file; use the corresponding variable for your configured credential name.

```sh
python scripts/deploy_test_bundle.py \
  --bundle ./candidate --engine compose --name example-test \
  --context your-test-docker-context \
  --assurance-config examples/assurance/stripe-kyc.json --output ./compose-plan

python scripts/deploy_test_bundle.py \
  --bundle ./candidate --engine compose --name example-test \
  --context your-test-docker-context \
  --assurance-config examples/assurance/stripe-kyc.json --output ./compose-apply \
  --apply --timeout 180
```

Plans run Compose normalization and validate the exact component/image inventory,
healthchecks and assurance secret source/target mapping. Ambient Compose overrides
and `.env` discovery are disabled. Application uses the frozen normalized content,
without builds, and waits for health using
[`docker compose up --wait`](https://docs.docker.com/reference/cli/docker/compose/up/).
The CLI requires an explicit context; operators must ensure it is their intended
test target. Context names themselves are not an environment authorization system.

## Helm plan and apply

The selected name is both Helm release and Kubernetes namespace. Provision the
selected assurance Secrets (each with a `token` key) in that namespace before
apply, and any image-pull credentials required by the chart.

```sh
python scripts/deploy_test_bundle.py \
  --bundle ./candidate --engine helm --name example-test \
  --context your-test-kube-context \
  --assurance-config examples/assurance/stripe-kyc.json --output ./helm-plan

python scripts/deploy_test_bundle.py \
  --bundle ./candidate --engine helm --name example-test \
  --context your-test-kube-context \
  --assurance-config examples/assurance/stripe-kyc.json --output ./helm-apply \
  --apply --timeout 180
```

Helm plans render the verified chart locally. Apply uses
[`helm upgrade --install --wait`](https://helm.sh/docs/v3/helm/helm_upgrade/) with a
post-renderer that refuses any difference from the validated plan. Hooks and CRD
installation are disabled; a chart changing output between install and upgrade
must be independently planned and still pass the same image/namespace checks.
The implementation never extracts downloaded chart archives onto the filesystem.

The initial supported profile has exactly one Deployment/container per component,
with readiness and liveness probes, in the selected namespace. Service, ConfigMap,
PersistentVolumeClaim and NetworkPolicy objects are allowed; bundled Secrets,
cluster-scoped resources, hooks, Jobs, init containers, CRDs, aliases and duplicate
YAML keys are rejected. StatefulSets, migration jobs and more complex workloads
need additional explicit support and qualification. At least one plane adapter
must carry the selected assurance settings and exact external secret mounts.
Configuration matching does not prove that the adapter enforces assurance or that
a provider is reachable. Preserve default-off provider capabilities.

## Receipts and recovery

Each invocation reserves a new private output directory. A plan receipt records
`validated-plan`; a successful apply records `tool-wait-succeeded`, the candidate,
context, configured image digests and hashes of the rendered configuration,
assurance configuration and tool observation. Expanded child configuration is
captured, not printed to logs. Failed apply does not produce a success receipt;
resources may remain for diagnosis. Tool readiness is not business/provider
acceptance, and the receipt does not claim to independently verify image builds.

Use native tools against the exact context/name for status and diagnostics. To
reapply an older candidate, fetch its exact test tag into a new directory and
repeat plan/apply after checking data/migration compatibility. No automatic
rollback, volume deletion, production fallback or destructive cleanup is provided.

## Prepare Compose and Helm assets

For images that implement the [HTTP plane-adapter runtime contract](runtime-contract.md),
prepare a multi-adapter Compose definition and deterministic Helm chart:

```sh
python scripts/prepare_test_plane.py \
  --metadata ./metadata.json --runtime-config ./runtime.json \
  --assurance-config examples/assurance/stripe-kyc.json --output ./prepared
```

Metadata uses the fields described below. `runtime.json` must map every component
name to an explicit native image health command, for example
`{"api":{"healthcheck":["CMD","/app/healthcheck"]}}`. Supply a command actually
present in the qualified image; the generator does not assume curl, a shell or
Python exists. Generated services retain non-root/read-only defaults and selected
assurance secrets, publish no host ports, and preserve Kubernetes default-deny
policies. Add reviewed service-specific connectivity before assembling the final
bundle; provider egress and general traffic are not implicitly opened. The chart
uses the original test candidate as its chart version and keeps that identity on
byte-preserving promotion. No version tag or release is created by preparation.

This profile is for HTTP plane adapters. It does not turn arbitrary database,
worker or library images into adapters; those need their own qualified deployment
assets and supported workload handling.

## Assemble existing artifacts locally

A producer can assemble already-built Compose JSON and a Helm chart archive:

```sh
python scripts/assemble_test_bundle.py \
  --metadata ./metadata.json --compose ./compose.json --chart ./chart.tgz \
  --repository ghcr.io/your-org/your-plane --output ./oci-bundle
```

`metadata.json` contains `schema`, `candidate`, `source_revision` and `components`
from the bundle profile. The assembler computes asset descriptors, verifies the
result and writes an OCI layout and index. It does not rebuild assets, produce a
trusted channel selection, or publish/move a registry tag. Producer qualification
must establish dependency, source/image provenance and runtime compatibility.

## Qualification coverage

Unit tests cover token scope, credential stripping on redirects, selector and
candidate mismatch, digest substitution, partial downloads and deployment
rejection paths. CI also pulls an exact public GHCR fixture image, exercises real
Compose readiness/HTTP traffic, and tests Helm install, service HTTP traffic and
upgrade in a disposable kind cluster. The fixture has narrowly scoped DNS and
self-service connectivity alongside default deny. Only resources created by the
qualification harness are removed; it never deletes persistent application volumes.

The bundle transport in native runtime tests uses a controlled registry fixture;
real public GHCR manifest/blob reads were checked separately. Neither proves that
a real multi-service bundle has been published to GHCR or deployed. Fleet bundle
assembly/publication, private-registry access qualification, service-specific
configuration/migrations, multi-service compatibility and provider/runtime
acceptance remain tracked in [deployment issue #20](https://github.com/hyperme-sh/spanopticon/issues/20).
