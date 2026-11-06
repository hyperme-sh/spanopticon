# Bring your own settlement processor

Run this adapter as a separate process owned by your network. The public
`contract.py` reference implements `SettlementPort.settle` and the existing
`controlplane.settlement.intent.v1` wire contract using only Python's standard
library. It does not require a private package, registry credential, or provider
account. Existing billing consumers communicate with it over HTTP.
Submit a signed intent containing wallet, atomic integer amount, asset, network,
and idempotency key. The result has status `accepted`, `refused`, or `pending`
and an opaque `processor_reference`. A pending result is not delivery or payment
confirmation. Never infer status from the reference string.

`Adapter` checks signer authority before invoking the processor. Supply a verifier
that checks the canonical intent signature and the signer's authority over the
wallet. `Processor` is the interface for your own rail. Persist its idempotency
binding before effects; retries after a crash must recover the same operation.
The included `TestProcessor` and HMAC verifier are process-local test doubles.
They do not move funds and are not production persistence or wallet cryptography.

Set `SETTLEMENT_TEST_LEDGER_FILE` to a writable SQLite file to select
`DurableTestProcessor` in the HTTP server. Mount its parent directory on persistent
storage. This processor accepts only asset `TEST` on network `local`; it commits
the simulated operation and the signer/idempotency binding in the same database
transaction. A retry after process or container replacement returns the original
receipt; changing the signed payload for that key returns HTTP 409. Verification
still runs before replay lookup, including after restart. Losing the ledger loses
this guarantee. `/health/ready` reports whether durable mode is active.

The local ledger is the entire simulated effect. It does **not** demonstrate
exactly-once payment on an external rail. A real processor must reconcile ambiguous
network responses using that rail's durable operation identity before retrying.
An `accepted` TEST receipt confirms only a local ledger entry, never a transfer.

Run the sample tests from the repository root with
`python -m unittest discover -s examples/settlement -v`. They exercise independent
process contention, restart recovery, conflicting replays, and authority rejection.

## Run the public sample

From the repository root, run:

```sh
python3 -m examples.settlement.check_sample --receipt /tmp/settlement-receipt.json
```

This builds the sample from public dependencies, generates temporary TEST keys,
starts Compose on an ephemeral loopback port, submits a signed request, replaces
the container, and verifies replay and rejection behavior. It removes the
containers afterward and retains the TEST ledger volume. The receipt contains no
credentials. This command also runs in public PR CI.

For a manually managed instance, supply `SETTLEMENT_TEST_KEYS_FILE` as the absolute
path to a JSON list of `{ "signer": "...", "wallet": "...", "key_hex": "..." }`
TEST credentials. Make the file readable by container UID 10001, keep its parent
directory private, then run `docker compose -f examples/settlement/compose.yaml
up --build --wait`. The default address is `http://127.0.0.1:18081`. This local
sample does not provide internet-facing TLS or production wallet authentication.

Consume authenticated `hypermesh.lease.outcome.v1` envelopes using the registered
schema and tenant scope. Apply your own settlement policy; the outcome is a fact,
not an instruction granting authority over a wallet. Keep the processor in your
own service and communicate across service boundaries using HTTP or events.

## Branding

You may describe compatibility with the Spanopticon protocol, including its
Hypermesh-named wire contracts. Hypermesh and Fyber are Fyber trademarks and are
not available as your fork's product name, host name, or marketplace name.
Compatible networks may state that they speak the protocol.
