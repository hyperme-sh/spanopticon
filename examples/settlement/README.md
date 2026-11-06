# Bring your own settlement processor

Run this adapter as a separate process owned by your network. Install the exact
qualified `controlplane-billing` wheel; the interface is `SettlementPort.settle`.
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

Consume authenticated `hypermesh.lease.outcome.v1` envelopes using the registered
schema and tenant scope. Apply your own settlement policy; the outcome is a fact,
not an instruction granting authority over a wallet. Keep the processor in your
own service and communicate across service boundaries using HTTP or events.

## Branding

You may describe compatibility with the Spanopticon protocol, including its
Hypermesh-named wire contracts. Hypermesh and Fyber are Fyber trademarks and are
not available as your fork's product name, host name, or marketplace name.
Compatible networks may state that they speak the protocol.
