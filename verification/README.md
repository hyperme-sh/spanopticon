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
