# Cedar: a public self-use operator sample

Cedar is an independent reference operator for the public protocol. It uses its
own identity issuer, organization membership, host enrollment and lease state.
It is not the Hypermesh Plane binary or a Fyber marketplace deployment. All
source and hashed dependencies needed to run this example are public.

From the repository root, with Docker Compose and OpenSSL installed:

```sh
python3 -m examples.operator.check_sample --receipt /tmp/cedar-receipt.json
```

The command builds four restricted containers on an internal network with no
published ports. The qualification driver makes HTTP requests inside that network.
Each service gets only
its own secrets; the operator receives the issuer's public key, never its signing
key. Generated high-entropy TEST login credentials are temporary. This password
issuer is a local test fixture, not a production account or OIDC login service.

The run verifies this sequence:

1. An identity with a tenant and configured organization signs in. The operator
   verifies the Ed25519 signature, issuer, audience, expiry and tenant, then checks
   its membership permissions. Other tenants and organizations are denied.
2. The owner registers a host and receives a one-use enrollment token. The host
   container generates its own X25519 key and device credential, durably writes
   them in a mode-0600 file, then enrolls and heartbeats. Private host keys stay on
   that host. A consumed token alone cannot enroll again: recovery requires the
   bound credential and the exact tenant, token, host name and public key. The
   smoke discards the committed reply and terminates the host, replaces the
   operator, and verifies that the restarted host recovers the same identity.
   Changed retries and person tokens at device endpoints are rejected.
3. The same organization creates a one-hour self-use reservation. A heartbeat is
   required. The host executes a bounded CPU subprocess inside its restricted
   container and reports the result with its device credential. A durable lease
   transition stores the registered `hypermesh.lease.ended.v1` event atomically.
4. The separate settlement process accepts a signed intent under the explicit
   sample policy: one atomic unit of `TEST` on `local` per completed job. This is
   a test grant from the configured signer, not a lease charge or host payout.
   The lease retains zero gross cents and no billing record. An event by itself
   grants no wallet authority.
5. The adapter is held offline during completion. Replacing the operator retains
   the pending event. Restoring the adapter delivers it; simulating a lost reply
   and replacing both processes preserves the receipt and exactly one TEST entry.

The receipt contains the event, image identities, and qualification results, with
no credentials. Containers are removed on success or failure. Named TEST volumes
are retained; they are not production data or physical host qualification.

## Contracts and limits

Enrollment uses the public `enroll_token`, `host_name`, `wg_public_key` and
`device_secret` vocabulary. Cedar's device creation and job APIs are deliberately
small reference APIs, not a replacement for every Plane or native host endpoint.
The completion payload is validated against the registered public lease-ended
JSON schema bundled under `schemas/`; its source revision and digest are recorded
in `schema-source.json`. The settlement HTTP contract is the public
[SettlementPort reference](../settlement/README.md).

The example proves protocol identity, ownership, enrollment, actual CPU work,
durable lifecycle delivery and TEST settlement. It does not configure a WireGuard
data plane, execute arbitrary renter images, certify hardware, or qualify the
native host installer. There is no claim of production isolation or a real
transfer. A production operator supplies its own identity/account lifecycle,
host admission, workload isolation and rail reconciliation policies.

Hypermesh and Fyber are Fyber trademarks, not available as a fork's product,
host, or marketplace name. A compatible network may describe protocol
compatibility, including the Hypermesh-named wire contracts used here.
