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

## Deployment assurance

Every generated plane must explicitly select `stripe-kyc`, `verification`, or
`both` through `--assurance-config`. An absent selection, empty provider map or
incomplete selected provider is rejected before any output is created. Each
provider declares an HTTPS owner-service URL and an external credential secret;
credentials are never copied into generated files. The Stripe URL is the payment
owner that evaluates Stripe KYC, not a replacement identity authority or a raw
Stripe API key. Verification uses its own owner service.

The image must consume `SPANOPTICON_ASSURANCE_MODE`,
`SPANOPTICON_STRIPE_KYC_URL` / `SPANOPTICON_VERIFICATION_URL` and the corresponding
`*_TOKEN_FILE` paths. Compose requires `SPANOPTICON_SECRET_<SECRET_NAME>_FILE` (uppercase, hyphens
replaced by underscores) to point to an operator-provisioned file readable by
UID 65532. An absent or empty path fails Compose interpolation. Kubernetes
requires a Secret with a `token` key for each configured credential. Mounts are
read-only. Rotate mounted Kubernetes subPath secrets by restarting the workload.
These settings are a plane-adapter contract, not existing payment-service flags.
Do not assume an arbitrary component image implements them. Runtime integration
must qualify the adapter before deploying these templates with user traffic.

With `stripe-kyc`, verification is optional and is not configured or called by
this policy. With `verification`, a Stripe KYC authority is not required for
this assurance prerequisite. `both` explicitly requires both authorities;
unavailability must not silently downgrade the chosen policy. An operator who
wants Stripe alone chooses `stripe-kyc`, rather than relying on outage fallback.

A configured endpoint and secret reference establish configuration completeness,
not provider availability or user verification. The plane adapter must refuse
readiness if a selected authority is unavailable, unauthenticated or not
configured upstream, and must deny admission on missing, stale or unavailable
subject/tenant-bound evidence. Merely accepting these environment variables is
not runtime acceptance. IPinfo being absent remains unavailable, never allow.
Stripe payout eligibility, geographic restrictions, closure and maintenance
fences remain independent and mandatory where applicable. Verification evidence
does not establish Stripe KYC or authorize payouts. Paid SMS remains default-off.
