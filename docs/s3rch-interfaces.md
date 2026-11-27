# s3r.ch external interfaces

Spanopticon supplies s3r.ch's interfaces outside its own network. Its edge routes
each interface to the independently owned service; a service repository's
existence does not establish implementation of a particular contract.

## Private contact lanes

The ownership chain for SociACL #71 is:

| Component | Responsibility |
| --- | --- |
| SociACL | Lane derivation and signed/sealed recipient grants; client authority checks |
| s3r.ch | Ciphertext transport adapter, client sealing/opening and sender verification |
| controlplane-edge | Public GET/POST route, anonymous source rate limits, body cap, credential stripping |
| acl-service | Opaque ciphertext append/read, atomic lane/global quotas, expiry and retry semantics |
| controlplane-db | Additive platform table/index migration |

The external URL is
`https://<spanopticon-origin>/api/v1/acl/sociacl/contact-lanes/<64-lowercase-hex-laneId>/messages`.
It uses the existing `CPEDGE_ACL_SERVICE_URL` upstream. Add the s3r.ch origin to
`CPEDGE_CORS_ORIGINS`; keep the ACL upstream private. Neither the browser nor the
lane table needs a tenant/account association. The edge strips credentials from
lane forwarding and excludes lane paths from tenant access events. Uvicorn
redacts lane access paths; infrastructure access logging must also redact them.

The database delivery must include `platform_0086_contact_lanes` before lane use.
Grant table access only to the ACL runtime role and migration owner. A missing
schema fails closed. TEST services continue to follow their moving channels;
there is no matching-build requirement between edge, ACL and s3r.ch.

POST carries only raw `application/octet-stream` ciphertext (32–65536 bytes).
GET returns a bounded list of SHA-256 IDs and standard-base64 ciphertext. The
service caps a lane at 128 messages / 1 MiB, 30 new messages per rolling minute,
and seven-day retention. Global caps are 4096 messages / 64 MiB. Retries preserve
the original expiry. No enumeration or account lookup is exposed.

Storage cannot authenticate an arbitrary ciphertext sender. s3r.ch must verify
the recipient-issued lane grant before use, seal signed messages, and decrypt
and verify incoming signatures/current authorization before rendering. A request,
write, lookup or digest match grants nothing. First-contact request envelopes
remain a separate contract from accepted-contact lanes.

References: [SociACL #71](https://github.com/FyberLabs/SociACL/issues/71),
[database #40](https://github.com/hyperme-sh/controlplane-db/pull/40),
[ACL #70](https://github.com/hyperme-sh/acl-service/pull/70),
[edge #55](https://github.com/hyperme-sh/controlplane-edge/pull/55), and
[s3r.ch #339](https://github.com/FyberLabs/s3r.ch/pull/339).


## Public first-contact requests

The separate SociACL #70 interface uses
`https://<spanopticon-origin>/api/v1/acl/sociacl/first-contact/<64-lowercase-hex-channelId>/messages`.
It follows the same edge-to-ACL ownership chain, upstream and CORS configuration.
It requires the additive `platform_0087_first_contact` database migration and
uses its own table, global quota and edge concurrency budget. TEST channels
remain independent and moving; a missing schema or upstream refuses requests.

POST carries 94–8192 bytes of opaque encrypted request data. GET uses the same
bounded SHA-256 ID and standard-base64 response shape as private lanes. Each
channel allows 128 messages / 1 MiB and ten new messages per rolling minute;
the separate global budget is 4096 messages / 32 MiB, with seven-day retention.
Exact retries keep the original expiry. The edge limits this interface to eight
in-flight requests, strips credentials, applies anonymous source throttling and
redacts channel paths from logs and tenant events. Infrastructure access logging
must also redact channel identifiers.

The s3r.ch adapter uses this Spanopticon URL, omits cookies and credentials,
rejects redirects and verifies ciphertext digests. Public request data does not
identify an account to storage. Clients still need authenticated fresh descriptor
discovery, current sender/history verification, replay admission and explicit
recipient acceptance. A successful append never grants a private lane. Product UI,
descriptor publication and grant-outbox delivery are separate remaining work.

References: [SociACL #70](https://github.com/FyberLabs/SociACL/issues/70),
[database #44](https://github.com/hyperme-sh/controlplane-db/pull/44),
[ACL #72](https://github.com/hyperme-sh/acl-service/pull/72),
[edge #57](https://github.com/hyperme-sh/controlplane-edge/pull/57), and
[s3r.ch #367](https://github.com/FyberLabs/s3r.ch/pull/367).

## Squad identity, sessions and service credentials

s3r.ch treats the SEA public key as the durable identity. Wallet and IdP
sign-ins resolve to that same principal through the ACL credential-source
contract; they do not create a second account id. One site session manager
issues browser sessions, verifies linked-login proofs and mints narrowly scoped
access JWTs for delegated agents. Those agent tokens carry explicit scopes and
do not inherit the owner's forum object grants.

Squad's ACL authority and Spanopticon storage both use tenant-bound service API
keys exchanged at the edge for short-lived bearer tokens. The pilot requires one
shared tenant UUID across the statement source, identity replay store, Plane
binding and optional record stores. Keys are scoped by permission (`acl:*`,
`storage:*`), not by forum or Squad instance: provisioning selects a graph scope
such as `forum`, but credentials are not issued per forum or per roster.

Server-only adapters send `X-Api-Key`, matching `X-Tenant-ID`, `redirect: error`,
and omit cookies. The edge removes the raw key before upstream forwarding.
Separate keys may exist for read versus write on the same journal (for example
delegation enrollment), but all remain tenant-level service credentials rather
than per-consumer secrets.

References: [s3r.ch configuration](https://github.com/FyberLabs/s3r.ch/blob/master/docs/configuration.md),
[s3r.ch Squad pilot](https://github.com/FyberLabs/s3r.ch/blob/master/lib/squad-pilot-server.ts),
[edge statement admission](https://github.com/hyperme-sh/controlplane-edge/blob/main/src/controlplane_edge/edge_proxy.py).

## ACL signed graph sources (Squad)

Squad reads forum ownership, grants, agent delegations and linked-login history
through three private ACL transports on the Spanopticon origin. Each is
provisioned per `(tenant UUID, authenticated service subject, scope)` binding on
the ACL owner; absent bindings return 503 and mismatched subjects return 403.

| Surface | Caller | Credential scope |
| --- | --- | --- |
| Statements | s3r.ch server (`createAclGraphSource`, scope from `S3RCH_STATEMENT_SOURCE_SCOPE`, typically `forum`) | `acl:statements:read` (snapshot), `acl:statements:write` (append, `forum-update`) |
| Delegations | s3r.ch server (agent sessions, Squad enrollment) | `acl:delegations:read` (snapshot), `acl:delegations:write` (append) |
| Credential source | s3r.ch server (linked SEA issuance, optional) | `acl:identity:credential-source` |

Base path pattern: `https://<spanopticon-origin>/api/v1/acl/sociacl/{statements|delegations}/{scope}/{operation}`.

### `POST .../statements/{scope}/snapshot`

**Caller:** s3r.ch server adapter before forum authority refresh, Squad pilot
readiness and bot statement admission.

**Request:** JSON `{"evaluatedAt": <non-negative integer second>}`. Uncompressed
`application/json` only; edge buffers up to 70 000 bytes with a 1.5 s body
deadline and per-tenant concurrency (two in flight, eight global across statement
and delegation traffic).

**Response (200):** JSON with `fence` (64 lowercase hex) and `snapshot`
(`completeThrough`, `history`, `rows` for statements). `Cache-Control: private, no-store`.
Consumer binds the fence as `{evaluatedAt}.{fence}` for append and `forum-update`.

**Errors:** 403 missing binding, wrong scope, or refused admission; 409 history
conflict (fence stale); 413/415/400 malformed body; 429 rate or capacity;
503 unavailable source or timeout. Client maps 409 to a conflict that requires a
fresh snapshot at the current second.

### `POST .../statements/{scope}/append`

**Caller:** s3r.ch server when admitting signed forum rows (for example bot
statements, Squad enrollment) after a current snapshot read.

**Request:** JSON `{"evaluatedAt", "fence", "rows"}` where `fence` is the hex
portion from the bound read, and `rows` is a bounded signed row array (at most 65
rows, 64 KiB encoded).

**Response (200):** `{"ok": true}`.

**Errors:** Same family as snapshot; 409 indicates the source changed and the
adapter returns `false` without automatic retry.

### `POST .../statements/{scope}/forum-update`

**Caller:** s3r.ch server when a signed bot delivery must commit through Plane
inside the ACL transaction (`createAclGraphSource` `commitForumUpdate`).

**Request:** JSON `{"evaluatedAt", "fence", "rows", "update"}` with a structured
`update` object (`tenant`, `forumId`, `botId`, delivery fields, `statementId`,
`status`, signatures). Rows may be empty when only the guarded update is admitted.

**Response (200):** `{"ok": true}` on commit.

**Errors:** 403 refused authority; 409 conflict; client also treats transport
failures as `refused` or `conflict` without blind retry. ACL may call Plane
`authorize-update` under an `hm_fcm_` commit credential before finishing the
append.

### `POST .../delegations/{scope}/snapshot` and `.../append`

**Caller:** s3r.ch server for agent delegation journals (`createAclDelegationSource`,
Squad agent enrollment with a separate write key).

**Contract:** Same transport shape as statements. Delegation snapshots include
`audience` on the graph document in addition to `completeThrough`, `history` and
`rows`. Statement scopes do not authorize delegation operations and vice versa.

### `POST /api/v1/acl/sociacl/credential-source/read`

**Caller:** s3r.ch server (`createAclCredentialSource`) when
`S3RCH_LINKED_IDENTITY_CONFIG_FILE` is configured.

**Request:** JSON `{"credential", "evaluatedAt"}` where `credential` is either
`{kind: "wallet", address}` or `{kind: "oidc", iss, tenant, sub}`.

**Response (200):** Versioned envelope with `tenant`, `source` (authority-domain
UUID), `authorityDigest`, configured `trust`, and `snapshot` containing the echoed
credential, matching `links`, `revocations` and complete `history` for enrolled
identities in that domain. `Cache-Control: private, no-store`.

**Errors:** 403 refused or missing binding; 409 credential state changed; 413 body
over 4096 bytes; 415 non-JSON; 429 busy; 503 unavailable. Consumers re-verify
trust locally and must not cache the response as standing permission to issue a
session later.

References: [acl-service statement source](https://github.com/hyperme-sh/acl-service/blob/main/docs/statement-source.md),
[acl-service credential source](https://github.com/hyperme-sh/acl-service/blob/main/docs/credential-source.md),
[s3r.ch ACL graph adapter](https://github.com/FyberLabs/s3r.ch/blob/master/lib/acl-graph-source-server.ts),
[s3r.ch credential adapter](https://github.com/FyberLabs/s3r.ch/blob/master/lib/identity/acl-credential-source-server.ts).

## Spanopticon tenant storage (Squad)

Squad uses the storage owner's PostgreSQL-backed record and replay APIs on the
same Spanopticon origin as ACL. Routes forward through `CPEDGE_STORAGE_SERVICE_URL`.

### `GET|PUT|DELETE /api/v1/storage/records/{namespace}/{name}`

**Caller:** s3r.ch server (`createSpanopticonRecordStore`) for Squad project
state, visor companion sets, room webhooks, vault heads, and other configured
record namespaces (`cache`, `recovery`, `fixtures`, `media`, `vault`).

**Authentication:** Exchanged API-key bearer with tenant scope. Generic record
writes require `storage:records:write`; reads require `storage:records:read`.
Namespace-specific rules apply (`storage:media:*`, `storage:vault:*`, cache access
aliases). `X-Tenant-ID` must match the credential tenant. Bodies use
`application/octet-stream` for PUT (up to 8 MiB on named records). Conditional
`If-Match` / `If-None-Match` pass through; 412 signals `RecordConflict` to the
adapter.

**Response:** GET returns bytes with `ETag` quoted SHA-256 revision, or 404.
PUT returns 201 (create) or 204 (update) with new `ETag`. DELETE requires a
matching `If-Match` and returns 204.

**Errors:** 403 wrong scope or tenant; 404 missing record; 412 revision mismatch;
413 oversize; 503 upstream unavailable (adapter surfaces a single unavailable error).

### `POST /api/v1/storage/replays/{namespace}/{nonce}`

**Caller:** s3r.ch server (`createSpanopticonIdentityReplay`) for one-time identity
session challenges. The namespace is a 64-character lowercase hex digest derived
from the site origin (`identitySessionNamespace`).

**Authentication:** `storage:replays:consume` only; POST with empty body, canonical
`Content-Length: 0`, and `X-Replay-Expires-At` as Unix seconds. Other methods and
scopes are rejected at the edge before forwarding.

**Response:** 204 when the replay nonce is consumed inside the expiry window.

**Errors:** 403 non-service or wrong scope; 400 ambiguous headers or non-empty body;
405 non-POST; upstream enforces expiry and atomic rejection.

References: [edge storage routing](https://github.com/hyperme-sh/controlplane-edge/blob/main/README.md),
[s3r.ch record store](https://github.com/FyberLabs/s3r.ch/blob/master/lib/spanopticon-record-store-server.ts),
[s3r.ch identity replay](https://github.com/FyberLabs/s3r.ch/blob/master/lib/identity/spanopticon-replay-server.ts).

## Plane forum bot routes (Squad)

Plane holds bot assignment state. s3r.ch never forwards browser cookies to Plane;
only server-held credentials reach the configured Plane HTTPS origin.

### Owner-mediated forum API (Spanopticon edge to Plane)

After the site verifies the forum owner's SEA session and current graph grants for
a rostered bot principal, `createSquadPlaneForum` calls:

| Operation | Plane path | Authorization on the Plane request |
| --- | --- | --- |
| Task intake | `POST /api/v1/hypermesh/bots/{botId}/forum/tasks` | Owner bearer (`Authorization: Bearer …`) plus `X-Tenant-ID` |
| Run control | `POST /api/v1/hypermesh/bots/{botId}/forum/control` | Same owner bearer |
| Control read | `POST /api/v1/hypermesh/bots/{botId}/forum/control-current` | Scoped `hm_fop_` credential from pilot config |

JSON bodies are version 1 with `forum_id`, `source_task_id`, `assignment_id`,
`session_id`, and task-specific fields (`kind`, `workspace`, `request_sha256`, or
`action`). Responses return bound `task_id`, `status`, `desired` and `forum`
metadata; s3r.ch rejects responses that do not match the roster, tenant and
current ACL evaluation second.

**Browser entrypoints** (same site origin, not Spanopticon): `POST
/api/squad/pilot/plane/tasks`, `.../control`, `.../bot-controls` as documented in
[s3r.ch Squad plane forum](https://github.com/FyberLabs/s3r.ch/blob/master/docs/squad-plane-forum.md).

**Errors:** 400 malformed body; 403 failed session, roster or grant check; 404
missing control credential; 409 Plane conflict; 503 unavailable authority or
unexpected Plane payload (mapped from non-success HTTP).

### ACL-coordinated delivery authority

When admitting a signed forum delivery that must align with Plane assignment
state, the ACL statement source may call:

`POST https://<plane-origin>/api/v1/hypermesh/bots/{botId}/forum/authorize-update`

with an `hm_fcm_` commit credential configured in the statement binding's
`forumCommit` object. The body carries `version`, `forum_id`, `principal`,
assignment/session/task/delivery identifiers, `sequence`, `status`, and a digest
of the update. Plane returns `{"version": 1, "digest": "<sha256>"}` on success.
ACL maps Plane 403/409 to refused admission and other failures to transaction
rollback (HTTP 409/503 to the `forum-update` caller).

Agent heartbeat, claim and complete on
`/api/v1/hypermesh/bots/{UUID}/agent/*` use separate `hm_bot_` credentials at the
edge; they are not Squad owner forum intake and are omitted here.

References: [s3r.ch Squad plane adapter](https://github.com/FyberLabs/s3r.ch/blob/master/lib/squad-plane-forum.ts),
[acl-service forum commit](https://github.com/hyperme-sh/acl-service/blob/main/src/acl_service/infrastructure/forum_commit.py),
[edge bot credential forwarding](https://github.com/hyperme-sh/controlplane-edge/blob/main/README.md).
