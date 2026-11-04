# Generate and qualify a deployment

Use `scripts/new_plane.py NAME --image IMAGE@sha256:DIGEST --output DIRECTORY`.
Names are lowercase DNS labels up to 63 characters. Image tags alone are refused.
The output directory must not exist; existing deployments are never overwritten.
Both output files use JSON, which Docker Compose and Kubernetes accept directly.

## Local Compose

Inspect `compose.yaml`, configure required service settings through a separate
Compose override and validate the result:

```sh
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
default-deny NetworkPolicy for that namespace. It creates no public ingress,
credentials, storage, workload identity or cloud resources. The pod disables
automatic service-account token mounting, drops capabilities and uses the runtime
default seccomp profile.

Before applying, add narrowly scoped network policies for authorized callers,
DNS and required dependencies, plus reviewed credential/image-pull-secret and
configuration overlays. Use a network-policy-enforcing CNI. Validate with your
cluster version's schema and admission policies. Default-deny means the generated
service cannot receive ordinary application traffic until you authorize it.

Run server-side dry-run against the intended cluster only after reviewing the
context and namespace. Application readiness, migration/rollback compatibility,
resource sizing, backup restore and dependency outage behavior require integration
qualification. The example limits and probe timings are initial values, not
capacity claims.

## Release record

Record the image digest, configuration revision, dependency contracts, migration
plan, checks performed, deployment environment and rollback owner. Separate
local validation, CI results, deployment and observed runtime acceptance. Never
record credentials or private customer data in public release documentation.
