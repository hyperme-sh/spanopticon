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

The local worker alone mounts the mailbox volume. For test inspection, query its SQLite message table by challenge ID inside that container. Codes expire after ten minutes and the worker purges expired messages. Challenge issuance is limited to ten per scope per hour. Evidence lasts one day. The storage migration is explicit (`python -m verification.store`), transactional and safe to repeat. Schema versions beyond 1 require an updated migrator. The service database retains contact addresses and challenge metadata. See the expired-data command below for operator-controlled retention.

The API and worker use the public challenge core in `challenge.py`. SQLite serializes contact changes, failed attempts, success and worker claims. Local delivery commits the mailbox row and delivered marker in one attached-database transaction with SQLite's default rollback journals. Keep both attached databases on storage supporting SQLite locking and atomic commits. The sample sends no email or SMS.

## Kubernetes with an external issuer

Use `verification/chart` with a digest-pinned application image, the exact Keycloak realm issuer URL, a dedicated verification audience, and an existing Kubernetes Secret containing the realm RSA public key as `issuer.pub` and the service's 32-byte secret as `secret`. The API checks RS256 tokens with a subject and tenant claim. Provision and rotate the trusted public key through the Secret and restart the deployment after rotation.

The chart uses a single-replica Recreate deployment. Its init container applies migrations before the API and worker start. Both use SQLite on one ReadWriteOnce PVC, with a separate mailbox subdirectory mounted only by the worker. Use a storage class with SQLite-compatible locking. Helm uninstall removes the chart-managed claim. The API is a ClusterIP service. The chart requests egress denial for the pod, which requires a network-policy-capable CNI for enforcement.

Run `bash verification/qualify-helm.sh` from the repository root with Docker, kind, kubectl, Helm, jq, OpenSSL and Python 3. It exercises the full local-delivery journey in a disposable cluster using genuine Keycloak client credentials. The test issuer listens on host loopback and its public key is provisioned before installation.

## Operator delivery (Compose)

Start an adapter under your control on the Linux Docker host. It listens on an AF_UNIX SOCK_SEQPACKET socket named `delivery.sock` in a trusted directory. Restrict directory and socket permissions to the operator and worker UID (10001) or a dedicated group. A process with socket access can impersonate a delivery client or read submitted codes. Keep the adapter's provider credentials and logs outside the verification containers. Avoid logging request payloads.

Set `VERIFICATION_ADAPTER_DIRECTORY` to the absolute socket directory, then add `-f verification/compose.operator.yaml` after the base Compose file on setup and runtime commands. The overlay selects operator mode on the API and worker and mounts the socket directory only into the worker. Schema migration 002 preserves earlier rows as local-test delivery. Stop old API and worker processes before migrating a persistent deployment and back up its database first. Helm operators can deploy the same socket protocol with the sidecar configuration below.

Each request is one UTF-8 JSON packet with version (integer 1), challenge_id (UUID and adapter idempotency key), recipient, six-digit code and expires_at (Unix seconds). Accept only unexpired requests. The adapter must deduplicate challenge IDs and return one JSON packet of at most 1024 bytes containing version, the matching challenge_id, and status `accepted` or `rejected`. Acknowledge acceptance after the adapter has durably accepted responsibility for delivery. Completion still requires presentation of the correct code. Evidence records `operator-delivery` separately from historical local-mailbox evidence.

The worker commits its claim before calling the socket. A rejection fails closed. Timeout, disconnect and invalid acknowledgements leave an ambiguous outcome. A crash after claiming can leave the row claimed until expiry. None of these outcomes schedules a second delivery or enables completion. The operator can reconcile by challenge ID with its own adapter. A new challenge needs a new idempotency key and consumes the issuance budgets.

Synthetic .test addresses are enabled by default. In operator mode `VERIFICATION_RECIPIENT_DOMAINS` accepts a comma-separated allowlist of lowercase exact domains for other recipients. The API enforces rolling one-hour limits per authenticated subject scope (10), per literal lowercase recipient across subjects and tenants (3), and for the entire shared service database (100). The Compose overlay accepts positive integer overrides through VERIFICATION_LIMIT_SUBJECT, VERIFICATION_LIMIT_RECIPIENT and VERIFICATION_LIMIT_OPERATOR. The existing five-attempt completion limit remains per challenge. Exact idempotent retries consume no extra budget. Alias normalization beyond lowercase input and coordination across independent service databases are operator concerns.

Run `python3 -m verification.qualify_operator` on Linux with Docker Compose to exercise a separately running socket adapter, successful proof, explicit rejection, a lost acknowledgement, restart and cross-subject budget exhaustion. The fixture uses synthetic addresses and holds codes in memory.

### Reconcile an uncertain delivery

From the deployed Compose project, use the same configuration, project name and environment as at startup. Look up the challenge ID from the issuance response.

```sh
docker compose -f verification/compose.yaml -f verification/compose.operator.yaml \
  exec -T worker python -m verification.inspect_delivery "$CHALLENGE_ID"
```

The lookup opens the database read-only and returns the ID, dispatch status, challenge state and Unix expiry. A missing operator delivery exits with status 1. Check the operator adapter's durable receipt under the same ID. Keep recipients and codes out of incident records.

A `claimed` dispatch may still be running. Repeat the lookup after the worker finishes or recovers. If it remains claimed, or is `ambiguous`, record whether the adapter accepted, rejected or has no record of that ID. Even an adapter acceptance receipt establishes only delivery responsibility. Leave the challenge and dispatch rows intact. If the user still needs proof, have them request a fresh challenge with a new request key and complete that challenge within its expiry. Replaying the old request key returns the original challenge. Normal issuance limits apply.

## Operator delivery on Kubernetes

Use the verification chart with Kubernetes native sidecar support. The default delivery mode is the local mailbox. To run an operator adapter, supply these additional Helm values:

```yaml
delivery:
  mode: operator
  adapterImage: registry.example.org/team/adapter@sha256:<64 lowercase hex digits>
  startupCommand: ["/app/adapter-health", "socket-ready"]
  existingAdapterSecret: provider-credentials
  recipientDomains: [example.org]
  hourlyLimits: {subject: 10, recipient: 3, operator: 100}
```

Implement the socket protocol documented above. The chart sets VERIFICATION_ADAPTER_SOCKET to /adapter/delivery.sock. The adapter runs as UID/GID 10001 with a read-only root filesystem and a writable memory-backed socket directory. Its startup command must succeed only once the listener can accept work. The worker starts after that probe passes and mounts the directory read-only. The optional adapter-specific Secret mounts as files under /credentials only in the adapter. The adapter receives neither the API keys nor the verification PVC.

The existing pod egress-deny policy also applies in operator mode. A local adapter needs only the Unix socket. For a network provider, the cluster operator must configure suitable outbound access with a policy-capable CNI. Kubernetes NetworkPolicy egress grants apply to the entire pod. Scope access to the required gateway or move provider transport behind a separately secured relay where appropriate.

Run `bash verification/qualify-helm.sh operator` with the prerequisites above. The CI fixture is a separate digest-pinned image using synthetic addresses. The test obtains Keycloak tokens, checks accepted, rejected, timed-out and disconnected sends, and inspects the adapter's test receipts after a worker container restart. The fixture exercises all three hourly budgets against the shared database.

### Rehearse a cold restore in Compose

Run `python3 -m verification.qualify` from the repository root. In its disposable project, the rehearsal stops the API and worker, archives both SQLite volume directories together, removes the project volumes, and restores the archive into fresh volumes before starting migration and services. It reuses the original issuer and verification key. It checks tenant-scoped evidence, rejection of an already consumed code and completion of a pending challenge using its restored mailbox entry.

The archive contains contact data and short-lived codes. The rehearsal uses synthetic contacts and a temporary archive. For an operator backup, protect the archive and retain the corresponding configuration and keys separately. Restore into isolation first. The snapshot reflects the instant writers stopped. Reconcile later contact changes, deletions, consumed challenges and adapter deliveries before serving traffic from an older snapshot.

### Recover an operator verifier after database rollback

Keep the restored API, worker and adapter disconnected from traffic. Restore the service database to a fresh volume, retain the operator's independent delivery receipts, and run the migration job. Then run the following once against the restored database using the same Compose project and configuration as the migration.

```sh
docker compose -f verification/compose.yaml run --rm --no-deps migrate python -m verification.recover_operator
```

This offline command invalidates all pending challenges and clears verification evidence in one transaction. A queued row in an old snapshot may already have reached the provider. The worker skips invalidated rows, and a receipt alone cannot restore contact verification. The command preserves challenge IDs, stored request keys, attempt counts and the snapshot's dispatch statuses for inspection. Repeating it before restart is safe. Running it against a live database would invalidate current proofs.

Before resuming issuance, reconcile later provider receipts, erasures and request history against the snapshot. Missing post-snapshot rows also affect rate limits and idempotency. If you cannot reconstruct recent issuance counts, keep issuance paused for the full one-hour budget window. Require fresh verification for restored contacts. Provider-side message retention and recovery remain the operator's responsibility.

## Expired verification data

An operator can preview retention on the service database with `python -m verification.retention --retention-seconds 604800` inside the API or worker container. This example keeps rows for seven days after expiry. Choose the window to cover your incident-reconciliation needs. The minimum is one hour. Review the aggregate challenge and contact counts, then repeat with `--apply` to delete eligible rows in one transaction. The command recomputes eligibility at execution time.

Challenge deletion also ends lookup by the old challenge ID and idempotency key, including for ambiguous deliveries. Clients should use fresh request keys for new challenges. Active evidence, recent issuance counters and contacts referenced by retained challenges survive cleanup. Contact rows with evidence expire only after the selected window following evidence expiry. Run during a low-write period because apply takes the SQLite writer lock. This operation covers logical rows in the service database. Mailbox expiry runs in the local worker. Provider records, backups and subject-specific erasure have separate lifecycles.

### Erase one verification subject and recover a backup

Stop all verification API and worker replicas and finish any in-flight provider
request. On the operator host, record the exact token issuer, tenant UUID and
case-sensitive subject against the authoritative verification key file.

```sh
python3 -m verification.erasure record --authority "$authority" \
  --issuer "$issuer" --tenant "$tenant" --subject "$subject"
```

This permanently retires the issuer/tenant/subject combination in this installation.
The file now contains the original delivery key and the hashed scope. Keep it
in a protected directory and durably store the latest copy separately from
service and mailbox backups. For Kubernetes, replace the verification Secret's
original raw key value with these exact file bytes. Restrict access as for the
original key. An obsolete or lost authority makes recovery unsafe because an old
snapshot cannot identify subsequent deletions.

With writers still stopped, mount the current authority and the service volumes
in a fresh worker container. Run `python -m verification.erasure replay` in that
container, with the normal worker delivery mode and mailbox volume. Recreate
the API and worker so both mount the updated file. Repeat replay before every
restore goes online, including when restoring an older mailbox. Recovery drops
retired contact and challenge rows and local messages without a surviving
challenge. API and worker startup reject restored service rows pending replay.
The local worker also reconciles orphan messages before dispatch.

If switching delivery modes, reconcile every retained local mailbox in local
mode. Isolate archived snapshots from ordinary access until expiry. Apply the
provider's own erasure procedure to messages it has already accepted.

### Expire local backup archives

Choose a retention window in whole days for the installation. Keep completed
cold archives in an operator-owned, mode-0700 directory on a protected local
filesystem. Write each backup to a private temporary file and rename it into
place only after the backup command succeeds. Use the UTC capture time and a
fresh lowercase UUID without dashes in the final name, for example
`verification-20261107T120000Z-efad988e6f0d495ba9ed572eed74f482.tar`.
Preserve that name when copying an archive so its original expiry still applies.

Preview eligible archives with the command below. Add `--apply` to unlink them.
Schedule apply at the interval required by your retention policy. Every finalized
archive at or beyond the chosen age is eligible, including the last one.
The command scans only the specified directory and leaves symlinks, directories
and partial archives alone.

```sh
python3 -m verification.backup_expiry "$archive_directory" --retention-days "$retention_days"
```

Apply retention independently to replicas, object versions and storage snapshots.
Unlinking a local file does not erase those copies. Keep the current erasure
authority separately and replay it before bringing any restored archive online.

### Keep an off-host filesystem replica over SSH

Provision a dedicated backup account on a Linux SSH host with Python 3.11 or
later. Install the matching verification modules on its Python path, give the
account a private mode-0700 `archives` directory and pin its host key in the
client SSH configuration. Use encrypted storage and restrict the account to
the backup operator. Keep the trusted archive name, SHA-256 digest and current
erasure authority separately from this replica.

For a completed cold archive, use its original finalized name and trusted
digest. The following uses a configured SSH alias named `verification-backup`.
The receiver verifies the stream before atomically publishing a new archive.
A duplicate name fails without overwriting the existing copy.

```sh
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes verification-backup \
  python3 -m verification.backup_replica put archives "$name" "$sha256" < "$archive"
ssh verification-backup python3 -m verification.backup_expiry archives --retention-days 30
```

Choose the retention window for this host and schedule the same expiry command
with `--apply`. Its cutoff uses snapshot time even for a newly transferred
copy. For recovery, download into a new private temporary file. Check both the
SSH exit status and the independently held digest before restoring the archive
in isolation and replaying the current erasure authority.

```sh
umask 077
snapshot=$(mktemp)
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes verification-backup \
  python3 -m verification.backup_replica get archives "$name" "$sha256" > "$snapshot" &&
  printf '%s  %s\n' "$sha256" "$snapshot" | sha256sum --check
```

The CI rehearsal uses a separate SSH server container and filesystem, discards
the local archive and Compose volumes, and starts the service from the retained
replica. Configure expiry separately for any snapshots or object-store copies
of that filesystem.

### Expire versioned S3 backups

On the backup host, install boto3 and use an identity scoped to the backup
bucket and prefix. Preview with the command below. Choose the retention window
and schedule the same command with `--apply` to remove eligible version IDs.

```sh
python3 -m verification.s3_expiry "$bucket" 'verification/cold/' --retention-days 30
```

Use a dedicated, versioning-enabled prefix and finalized archive names as above.
Serialize pruning with replication and other writers to that prefix. The cutoff
uses the capture time in the archive name for all its versions. Data versions
are removed before delete markers. A failed deletion stops the run and can be
retried. Object Lock retention and legal holds remain effective. The operator
needs GetBucketVersioning, ListBucketVersions and DeleteObjectVersion access.

Record the exact bucket, key, version ID and SHA-256 at backup time in independent
custody. Fetch that version, verify its digest and replay the current erasure
authority before serving restored data. Test the procedure against each actual
storage provider and separately account for replicas and storage snapshots.

### Retain and recover verification archives in Azure Blob Storage

Use a private, versioning-enabled block blob container. Record the account,
container, blob name, upload version ID and SHA-256 independently of the archive.
The sample lifecycle rule in `azure/retention-rule.json` covers finalized archive
names under `verification-backups/cold/verification-`. Reserve that prefix for
completed uploads. Merge the rule into the storage account's existing lifecycle
policy and set the three retention periods to the installation's requirements.

The example uses 30 days since modification for current blobs and 30 days since
creation for previous versions and snapshots. Copying an old archive into Azure
starts a new storage-age window. Lifecycle processing is asynchronous. Account
soft-delete retention can keep expired bytes recoverable beyond that window.
[Azure lifecycle deletion](https://learn.microsoft.com/en-us/azure/storage/blobs/lifecycle-management-policy-delete)

Authenticate Azure CLI with the recovery identity and give it blob read access
on the backup container. Download the recorded version to a new file:

```sh
bash verification/azure/fetch-version.sh "$account" "$container" "$blob" \
  "$version_id" "$trusted_sha256" "$destination"
```

The command exposes the destination only after the bytes match the trusted
digest. Restore into isolation and replay the current erasure authority before
starting verification workers or serving requests.

The local Helm qualification also signs in a synthetic human through Keycloak
in headless Chrome using authorization code flow and S256 PKCE. To run the
local Helm check, install Chrome and Python Playwright 1.56.0 in the test
environment. The check exercises contact proof, rejects completion by another
subject, ends the Keycloak session and checks refresh denial. Verification
validates signed access tokens offline. The drill uses a 30-second client
token lifetime and checks API rejection after expiry.
