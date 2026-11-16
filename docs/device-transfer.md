# SEA device transfer composition

Plane owner: hypermesh-plane #141. Parent: Spanopticon #85. Owner packages remain
in SDK, SociACL, Auth, ACL and portal. The gateway transport companion is in
controlplane-edge; this generator does not implement identity verification.

`python -m deployment.device_transfer --config /private/pairing.json --output /private/generated-pairing`
produces an additive `compose.override.json`, shared `scopes.json` and safe
`capabilities.json`. The configuration example and consumer contract live in
hypermesh-plane `deployment/device-transfer.example.json`. Generation is offline,
refuses missing/unknown fields, and never contacts an owner or provisions data.
It does not read private file contents. Keep generated output private: the overlay
contains server configuration paths. Capabilities contain only scope metadata;
`configured: true` is explicitly accompanied by `ready: false`.

Supported clients are the hyperme.sh controlplane-portal account device page and
s3r.ch `/identity/devices`, each transferring within its own declared HTTPS
origin/application/tenant/audience. The shipped clients reject cross-application
transfers. `crossApplicationPairs` must therefore be explicitly empty; nonempty
pairs are refused until the owning clients provide that contract. No origin or
tenant is inferred from a browser request. Up to eight total scopes match Auth's
current bound; s3r.ch supports one scope and the portal supports several tenants.

The overlay augments existing `auth-service`, `acl-service`, `controlplane-edge`,
`controlplane-portal` and optional `s3rch` services. Merge it with the existing base
using the generated directory as Compose's project directory so `./scopes.json`
resolves there. Do not run the overlay alone. Base services retain moving TEST
image channels, owner URLs, health checks, databases and native login. No image
version/digest or matching peer build is inserted. The skill's generic image-pin
advice is superseded by the owner's moving-channel policy.

Auth's existing environment file supplies DATABASE_URL; install the Auth-owned
pairing migration with its existing migration command before enabling the config.
The shared scope file is mounted read-only by Auth and edge. Keep Auth cleanup
enabled until removed scopes' bounded retained data expires. ACL's private
mount contains `bindings.json` and the existing protected PostgreSQL/policy files,
all readable only by the owner process. Bindings reference container paths under
`/run/device-transfer-private`. Existing policy must contain matching scopes,
current trust/history, an unexpired window and activePrincipals synchronized with
account retirement. This generator neither fabricates nor broadens that policy.
Missing owner configuration or migration is unavailable, never ready.

The s3r.ch existing environment file supplies its configured HTTPS OIDC provider,
client settings and local session protection. Register its existing callback
`/api/identity/device/callback` with the owner; portal retains native OIDC login.
Both use ordinary person scopes `auth:device:transfer` and
`acl:identity:transfer`. No host setup tokens, copied sessions or API keys.
Portal consumes PORTAL_DEVICE_TRANSFER_CONFIG server-side and returns only the
verified current-account scope via its existing config BFF. s3r.ch consumes
S3RCH_DEVICE_TRANSFER_CONFIG server-side. Neither receives private mount contents.

Edge accepts only the declared POST relay actions and ACL state/current routes,
with exact Origin/scope checks. Sender/ACL calls forward the normal bearer and
matching tenant header; Auth/ACL remain the authentication/authorization owners.
Receiver bootstrap forwards only its pairing capability and public/ciphertext
body, never cookies, source bearer, API keys or arbitrary forwarding headers.
No other auth path is opened. Per-worker address rate and concurrency limits,
request/response byte limits, deadlines, no-store responses and path-redacted
access logs apply before owner work. Configure ingress not to log request bodies,
capability headers or query strings; normalize public Host/Origin forwarding to
the client BFFs. The narrow handler uses exact-origin CORS instead of the general
edge CORS policy, and excludes pairing from tracing and access-event publication.

Infra #1559 owns executable fixture setup, readiness and teardown plus scheduled
registration. Fixtures need existing people/tenants, protected complete key
history/policies, correct person scopes, independent browser contexts, OIDC form
login, native PRF and camera drivers. No hand checks, manual nightly dispatch,
identity creation on startup or TEST data resets. Unit/contract CI does not
establish deployment or scheduled readiness. Later nightly failures become new
linked issues; scoped implementation issues close at green merge.
