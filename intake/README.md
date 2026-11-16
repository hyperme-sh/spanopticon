# Human feedback intake

This service implements Spanopticon #80. Hypermesh #79 owns the client widget,
local preview and consent controls. Collection and GitHub forwarding default off.
No issuer, membership, GitHub App, database, listener, or shared fixture is
provisioned by this change.

## Authority and deployment

Use the existing issuer's short-lived machine credentials. Tokens must have the
configured signature algorithm, issuer and audience, plus `sub`, `tenant_id`,
`device_id`, `iat` and `exp`. Neither client-supplied tenant headers nor token role
claims grant membership. PostgreSQL stores the declared issuer/subject membership
and the `submitter` or `reviewer` role for each tenant. A reviewer credential is
used by the operator's human review client; do not wire acceptance to an automatic
rule. Submission and review are distinct permissions, not GitHub branch approval
settings. No manual JWT issuer is included.

Provision the database through its owning deployment configuration. Run
`python -m intake.admin migrate` with `INTAKE_ADMIN_DATABASE_URL_FILE` pointing to
a private migration credential file. Repeating this command accepts only schema
version 1. A separate runtime role must be `NOSUPERUSER NOBYPASSRLS`, have `USAGE`
on schema `feedback`, `SELECT` on `schema_version` and `member`, and
`SELECT, INSERT, UPDATE, DELETE` on `consent`, `consent_history`, `report`,
`decision`, and `erasure`. Do not grant schema ownership, role creation, `TRUNCATE`
or membership updates to the runtime. Every tenant table uses forced row security;
the application also binds tenant predicates. Startup refuses a bypass role.

Use `python -m intake.admin members --file /private/members.json` to apply declared
membership records containing `tenant`, `issuer`, `subject`, `role`, and `enabled`.
This command does not invent identities or generate credentials. Revocation sets
`enabled` false; authenticated privacy requests remain available to the original
subject. A device credential must represent the affected person's declared scope;
shared-machine credentials cannot assert another person's identity in a body.

Set `INTAKE_CONFIG_FILE` to a private copy of `config.example.json`,
`INTAKE_DATABASE_URL_FILE` to the runtime connection file, and
`IDENTITY_PUBLIC_KEY_FILE` to the issuer's trusted PEM verification key. Rotate
that public key through deployment and restart, as in the existing reference
service. Component-to-repository routes are operator configuration, never client
input. Build with `docker build -f intake/Dockerfile .`; the image has no publisher
credential. Running services use the operator's moving TEST channel.

`python -m intake` binds port 8086 on loopback by default. A non-loopback address
requires `INTAKE_WIREGUARD_INTERFACE` and an installed `ip` utility; startup checks
both the WireGuard interface type and address ownership. Wildcard binds are
refused. Keep the listener on the host's loopback or mesh namespace; HAProxy owns
TLS for outside-mesh clients, with Cloudflare only at the Fyber production edge.
Do not override the entry point with an unrestricted Uvicorn bind. Access logging
and proxy-derived identity are disabled. Apply edge request/concurrency limits.

## API contract

All `/v1` routes require the existing bearer credential. Request bodies reject
unknown properties and are bounded at 24 KiB, including streamed bodies. Responses
are not cacheable. Validation errors never echo rejected input.

| Route | Purpose |
| --- | --- |
| `GET /v1/consent` | Current device's human-report decision; absent means off, revision 0 |
| `PUT /v1/consent` | `enabled`, `expected_revision`, notice `human-reports-v1`, purpose `product-feedback` |
| `POST /v1/reports` | UUID `Idempotency-Key`; `consent_revision`, `category` (`bug`/`feature`), configured `component`, `title`, `body`, literal `preview_confirmed: true` |
| `GET /v1/reports` | Original subject's unexpired reports, at most 1,000; export is complete |
| `GET /v1/review` | Reviewer's tenant queue, oldest 100 records |
| `POST /v1/review/{id}/decision` | Reviewer submits `accepted` or `rejected` and literal `human_reviewed: true` |
| `POST /v1/review/{id}/publish` | Forward an accepted, unexpired report to its configured repository |
| `POST /v1/review/{id}/reconcile` | Resolve an uncertain send with `issue_number`; requires an exact remote content/marker match |
| `POST /v1/review/{id}/close` | Record closure and shorten accepted retention |
| `GET /v1/privacy/export` | Own reports, consent history, and destination index; no stored export file |
| `DELETE /v1/privacy/data` | `Confirm: delete-my-feedback`; block ingestion and erase own text, preserving unresolved destination status |

Clients must show the purpose, destination, retention, privacy contact and editable
sanitized preview before sending. They must stop locally on withdrawal, then sync
the revision. The API checks current consent even for idempotent retries; stale
revisions, device switches and withdrawal cannot resurrect an upload. Other
telemetry categories are not enabled by this consent. Rights operations do not
consume the submission rate budget.

Known emails, network addresses, paths, secrets, serial labels, code blocks and
labeled prompt/file excerpts are removed before persistence and again before
GitHub forwarding. Optional contact capture and attachments are not supported.
Free-text redaction cannot recognize every personal detail: clients must warn
against including it, and reviewers must reject unsafe reports. There is no
claim of automatic anonymization. Neither raw request bodies nor provider error
responses are logged or retained. Spam/link flooding is dispositioned before
review. Sanitized category/component/text fingerprints deduplicate within a
tenant; duplicate responses do not disclose another subject's report ID. Limits
are 10 submissions per subject/hour and 1,000 per tenant/hour.

## Forwarding and failures

Forwarding also requires `publish_enabled: true` and
`GITHUB_INSTALLATION_TOKEN_FILE`, a privately delivered, rotating GitHub App
installation credential restricted to the configured repositories. The application
does not mint or expose credentials. Use the App's issue permission; do not broaden
permissions automatically if publication or deletion is refused. The client uses
only `api.github.com`, ignores proxy environment variables, refuses redirects and
performs no implicit HTTP retries. Tenant, subject and device identifiers never
appear in issue content. A random intake ID binds the destination mapping.

A durable `publishing` state is committed before a GitHub write. A timeout or
uncertain response becomes `unknown`; a crash may leave `publishing`. Neither
state can be sent again. A reviewer can associate the exact existing issue using
the reconciliation route. If the outcome cannot be established, leave it visibly
unresolved for the operator. GitHub does not provide an issue-create idempotency
key, so this service does not claim exactly-once remote writes across arbitrary
provider failures.

## Retention and requests

The [managed telemetry design](https://github.com/hyperme-sh/hypermesh-docs/blob/main/design/managed-telemetry-privacy.md)
defines the limits. Pending/accepted reports expire within 180 days of collection;
closure shortens that to 90 days after first closure. Spam, duplicate and rejected
reports expire seven days after collection. Reads and review refuse expired
content before the sweep. Startup and a 60-second maintenance loop clear expired
text and fingerprints; local metadata without an unresolved destination is removed
after the 30-day restore-marker interval. No read or retry extends a deadline.
Inactive consent history is retained up to 24 months; erasure metadata is retained
for that period and while a processor action remains unresolved. Backups, external
tombstone replay and retention of backup copies belong to Infra; this service
creates no backups. Before enabling it, the deployment owner must configure the
30-day backup limit and replay current deletion markers before restored reads.

Deletion first disables matching consent and records an ingestion block, then
clears the subject's report text. Other subjects and tenants are untouched. A
published or uncertain issue remains an explicit external-pending destination.
Maintenance attempts GitHub's `deleteIssue` mutation only for mapped issues whose
intake marker still matches. Permission failures, modified/missing markers,
unknown send outcomes and uncertain deletion responses remain pending; they are
not treated as successful deletion. The operator must resolve those processor
cases. The response describes retained audit metadata. Repeating the deletion
request returns the current completion status. An external copy is never called
deleted merely because local text was removed.

The public policy/contact path and client privacy UI remain with Hypermesh #79
and the existing legal pages. This module changes no crash/canary collection,
production activation, or GitHub approval settings.

## Checks

`python -m unittest discover -s intake/tests -v` runs offline policy/API/provider
contracts. Synthetic signing keys, ASGI requests and a fake GitHub transport are
explicit substitutions. PostgreSQL contracts run only in the dedicated CI job,
using its disposable database and restricted runtime role; no shared resources
or live GitHub writes are used. The job also builds the locked service image.
Scheduled composed client coverage stays with Infra, separate from PR sign-off.
