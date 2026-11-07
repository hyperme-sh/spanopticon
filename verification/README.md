# Local contact verification

The service accepts signed access tokens, derives scope from issuer, tenant and subject, and persists contact challenges and evidence in SQLite. The worker delivers codes to a local test mailbox. A successful completion demonstrates access to that mailbox. The evidence method is `local-test-delivery`. Real contact assurance requires a delivery adapter that reaches the intended recipient.

Run the full disposable flow from the public checkout with Docker Compose, Python 3 and OpenSSL:

```sh
python3 -m verification.qualify
```

The harness creates fresh issuer accounts, signing keys and a 32-byte verification secret. It builds the pinned Python image with hash-locked public dependencies, runs migration 001, starts the API and network-isolated worker, checks the HTTP lifecycle, restarts the services and removes its own test volumes.

For a persistent local installation, generate the configuration once in a private directory:

```sh
export VERIFICATION_CONFIG="$HOME/.local-verification"
python3 profiles/minimum/setup.py "$VERIFICATION_CONFIG"
(umask 077; openssl rand 32 > "$VERIFICATION_CONFIG/secret")
chmod 0444 "$VERIFICATION_CONFIG/secret"
docker compose -p local-verification -f verification/compose.yaml build api
docker compose -p local-verification -f verification/compose.yaml up -d --no-build --wait
```

Keep this mode-0700 directory outside source control and preserve the secret across restarts. Only the individual files each container needs are mounted. Use `docker compose port` with the same project and compose file to locate the loopback API and test issuer endpoints. The sample issuer accepts the generated local accounts at `POST /token`. For another issuer configure the trusted public key, algorithm, issuer and audience on the API. Tokens need subject, tenant ID, issuance time and expiry claims.

| Authenticated request | Behavior |
|---|---|
| POST /v1/challenges | Send a JSON address under a .test domain and a UUID Idempotency-Key header. Repeating the same request returns the same ID and expiry. Changing the address with that key returns 409. A new challenge supersedes the previous one. Changing the contact clears evidence. |
| POST /v1/challenges/{id}/complete | Submit the six-digit code as a JSON string field named code. Success consumes the challenge exactly once. Foreign, undelivered, expired, locked and consumed challenges give the same rejection. Five unsuccessful eligible attempts lock a challenge. |
| GET /v1/evidence | Returns the authenticated subject's local verification method, verified flag and expiry. |

The local worker alone mounts the mailbox volume. For test inspection, query its SQLite message table by challenge ID inside that container. Codes expire after ten minutes and the worker purges expired messages. Challenge issuance is limited to ten per scope per hour. Evidence lasts one day. The storage migration is explicit (`python -m verification.store`), transactional and safe to repeat. Schema versions beyond 1 require an updated migrator. The service database retains contact addresses and challenge metadata. Set a retention and deletion policy before adapting it for real contacts.

The API and worker use the public challenge core in `challenge.py`. SQLite serializes contact changes, failed attempts, success and worker claims. Local delivery commits the mailbox row and delivered marker in one attached-database transaction with SQLite's default rollback journals. Keep both attached databases on storage supporting SQLite locking and atomic commits. The sample sends no email or SMS.

## Kubernetes with an external issuer

Use `verification/chart` with a digest-pinned application image, the exact Keycloak realm issuer URL, a dedicated verification audience, and an existing Kubernetes Secret containing the realm RSA public key as `issuer.pub` and the service's 32-byte secret as `secret`. The API checks RS256 tokens with a subject and tenant claim. Provision and rotate the trusted public key through the Secret and restart the deployment after rotation.

The chart uses a single-replica Recreate deployment. Its init container applies migrations before the API and worker start. Both use SQLite on one ReadWriteOnce PVC, with a separate mailbox subdirectory mounted only by the worker. Use a storage class with SQLite-compatible locking. Helm uninstall removes the chart-managed claim. The API is a ClusterIP service. The chart requests egress denial for the pod, which requires a network-policy-capable CNI for enforcement.

Run `bash verification/qualify-helm.sh` from the repository root with Docker, kind, kubectl, Helm, jq, OpenSSL and Python 3. It exercises the full local-delivery journey in a disposable cluster using genuine Keycloak client credentials. The test issuer listens on host loopback and its public key is provisioned before installation.

## Operator delivery (Compose)

Start an adapter under your control on the Linux Docker host. It listens on an AF_UNIX SOCK_SEQPACKET socket named `delivery.sock` in a trusted directory. Restrict directory and socket permissions to the operator and worker UID (10001) or a dedicated group. A process with socket access can impersonate a delivery client or read submitted codes. Keep the adapter's provider credentials and logs outside the verification containers. Avoid logging request payloads.

Set `VERIFICATION_ADAPTER_DIRECTORY` to the absolute socket directory, then add `-f verification/compose.operator.yaml` after the base Compose file on setup and runtime commands. The overlay selects operator mode on the API and worker and mounts the socket directory only into the worker. Schema migration 002 preserves earlier rows as local-test delivery. Stop old API and worker processes before migrating a persistent deployment and back up its database first. The Helm chart currently deploys the local mailbox mode.

Each request is one UTF-8 JSON packet with version (integer 1), challenge_id (UUID and adapter idempotency key), recipient, six-digit code and expires_at (Unix seconds). Accept only unexpired requests. The adapter must deduplicate challenge IDs and return one JSON packet of at most 1024 bytes containing version, the matching challenge_id, and status `accepted` or `rejected`. Acknowledge acceptance after the adapter has durably accepted responsibility for delivery. Completion still requires presentation of the correct code. Evidence records `operator-delivery` separately from historical local-mailbox evidence.

The worker commits its claim before calling the socket. A rejection fails closed. Timeout, disconnect and invalid acknowledgements leave an ambiguous outcome. A crash after claiming can leave the row claimed until expiry. None of these outcomes schedules a second delivery or enables completion. The operator can reconcile by challenge ID with its own adapter. A new challenge needs a new idempotency key and consumes the issuance budgets.

Synthetic .test addresses are enabled by default. In operator mode `VERIFICATION_RECIPIENT_DOMAINS` accepts a comma-separated allowlist of lowercase exact domains for other recipients. The API enforces rolling one-hour limits per authenticated subject scope (10), per literal lowercase recipient across subjects and tenants (3), and for the entire shared service database (100). The Compose overlay accepts positive integer overrides through VERIFICATION_LIMIT_SUBJECT, VERIFICATION_LIMIT_RECIPIENT and VERIFICATION_LIMIT_OPERATOR. The existing five-attempt completion limit remains per challenge. Exact idempotent retries consume no extra budget. Alias normalization beyond lowercase input and coordination across independent service databases are operator concerns.

Run `python3 -m verification.qualify_operator` on Linux with Docker Compose to exercise a separately running socket adapter, successful proof, explicit rejection, a lost acknowledgement, restart and cross-subject budget exhaustion. The fixture uses synthetic addresses and holds codes in memory.
