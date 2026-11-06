# Disposable core-plane deployment qualification

The Compose project and Helm chart deploy the exact core-only plane OCI image
pinned in `../owner-images/hypermesh-plane.json`. They create a fresh disposable
PostgreSQL database, explicitly provision platform/events/hypermesh owner chains,
and then run the image's normal guarded schema-startup entrypoint. Billing is
not installed. The test profile explicitly allows an empty database and uses an
ephemeral enrollment marker. It is not a production database or restore setup.

Compose requires random, test-only `TEST_POSTGRES_PASSWORD` and `TEST_TICKET_KEY`
values. Use URL-safe hexadecimal values for the password because it is included
in the database URL. No database port is published; the plane binds a random
loopback-only host port. The database lives on tmpfs and is discarded when its
container is removed. Do not store real hosts, tenant data or payments here.

The Helm chart requires `existingSecret`, with `postgres-password`, `ticket-key`
and `database-url` keys. The database URL must address `<release>-postgres:5432`,
database and username `plane_test`. Use a disposable namespace/cluster. PostgreSQL
uses an emptyDir; deleting/replacing its pod discards test data. The bootstrap
account is an administrator for this disposable database. These assets do not
qualify runtime least-privilege database access, RLS or persistent storage.

```sh
docker compose -f fleet/plane/compose.yaml up -d --wait
helm upgrade --install plane-test fleet/plane/chart --set existingSecret=plane-test --wait
```

Authenticate pulls for GHCR as needed. For offline qualification CI verifies and
loads the exact retained owner archive rather than requiring a registry pull.
`python3 scripts/qualify_plane_runtime.py --directory <verified-artifact> --engine
compose` checks catalog readiness, exact source identity, refusal of a tenant
header as authority, absent billing routes and plane restart. The `helm` engine
creates/removes a task-owned kind cluster and checks installation and an
idempotent chart upgrade. Generated test secrets and resources are cleaned up;
receipts contain no credential values. Chart version `0.0.0` is a source placeholder.

This is core database/startup qualification. External owner services, genuine
assurance, authorized tenant journeys, durable data, upgrades between distinct
versions, rollback, backup/restore and production configuration remain separate.
No fleet bundle inventory is admitted by these checks alone.
