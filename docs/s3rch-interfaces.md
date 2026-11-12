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
