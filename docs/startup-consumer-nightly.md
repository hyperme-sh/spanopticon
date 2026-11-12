# Scheduled startup consumer coverage

Infra #923 owns registration. `python -m startup.consumer_nightly` runs only in
the central scheduled `startup-consumers` matrix against current owner main.
Never execute or dispatch it manually. Local tests in
`tests/test_startup_consumer_contract.py` are protocol unit vectors with in-memory
responses; they do not contact services.

The Infra-owned private fixture extends the existing owner fixture with four
HTTPS service roots (`marketplace`, `keys`, `plane`, `gateway`), one declared
organization, a distinct negative tenant, existing listing/key/lease IDs, the key
prefix, its active catalog ID and a foreign catalog ID. The declared mapping must
include both an enabled and a disabled platform-admin scope, so cross-grant
leakage cannot pass merely because every expected grant is enabled. A separate existing API
key and a verified second broker login with the same email are required. Infra
stages the actual private credentials only after source acquisition, and removes
only its run-owned files. There is no resource creation, lease purchase, provider
provisioning, policy repair or shared-data reset in this harness.

| Case | Assertion |
|---|---|
| concurrent-restart | Two separate startup command processes share the real checkpoint lock, recheck owners and return the same person and complete checkpoint. |
| same-email-refusal | The second signed login passes Auth `/me`, has the same email and a distinct immutable identity, but receives the exact startup-identity refusal; the configured binding stays unchanged. |
| independent-grant-boundaries | ACL enforces each configured platform boolean and each explicit organization role; unrelated objects are denied. Keycloak's actual realm-management client role matches only its independent boolean. Auth grants no application permissions. |
| api-key-organization-isolation | The declared active renter key belongs to the selected tenant/organization, is absent from the other declared organization sessions and is refused under a foreign tenant header. |
| marketplace-person-scope | The existing listing has the declared ID, tenant and reconciled seller person; a foreign tenant header is refused. |
| plane-person-scope | Checkout resolves the reconciled renter; the existing lease matches person, organization and catalog, and foreign tenant selection is refused. |
| gateway-key-scope | Key-scope enforcement is enabled; the existing key sees its declared active catalog but not the foreign catalog, and cannot override its tenant. |

Missing private inputs or owner readiness block dependent cases. Once the shared
context is available, cases run independently, so one failure does not hide other
outcomes. JUnit preserves passed, failed and blocked results separately. Diagnostic
output never contains credentials, service bodies or private identity fields.

These checks establish consumer identity and scope behavior. They do not perform
model inference, qualify a physical host, publish a release or activate TEST.
Strix Halo mapping remains deferred. The separate Infra `startup-portal-browser`
case exercises the Portal UI through actual provider login and organization
selection; the existing s3r.ch browser case covers saved SEA-key preservation.
