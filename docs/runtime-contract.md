# Container runtime contract

The generated manifests assume a Linux application image that:

- Runs as UID/GID 65532 without elevated capabilities.
- Listens on TCP port 8080 on the container network interface.
- Serves unauthenticated `GET /healthz` for process liveness and `GET /readyz`
  for readiness; neither endpoint exposes configuration or secrets.
- Accepts a read-only root filesystem with writable temporary storage at `/tmp`.
- Handles SIGTERM gracefully and writes logs to stdout/stderr.

Readiness should fail when dependencies needed for traffic are unavailable.
Liveness should not restart an otherwise healthy process just because a remote
service is temporarily unavailable. Service-specific thresholds require load and
failure testing.

The manifests do not invent identity, tenancy, database or event credentials.
Configure these through reviewed service-specific overlays and your operator's
secret-management system. Never bake secrets into an image or commit them here.
Validate token issuer, audience, expiration and tenant scope at every authority
boundary. Default-off capabilities must remain disabled until their owners
qualify them.
