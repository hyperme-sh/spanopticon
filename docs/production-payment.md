# Production Payment configuration

Render the inactive plan from FyberLabs/infra `scripts/owner-apps/contracts.py`,
including `payment_worker` and `payment_events`, then use the same plan here:

```
python3 -m deployment.payment --plan /private/owners.json \
  --chart-version <existing-chart-version> --output /private/payment-output
```

This writes `compose.yaml`, `chart.tgz`, and `bindings.json`. It does not install,
publish, create secrets, or change any version. Supply the existing chart metadata
version. The generator rejects enabled billing. This is separate from the generic
TEST bundle installer and does not change its moving channel policy.

The output includes Payment HTTP, its worker, paid and archive relays, and the
Plane/User consumers. Images, public variables, commands, relay filters, file
mounts and resource limits come from the same Infra plan as Azure. No host ports
or external Kubernetes Services are created. Configure existing private service
routing and ingress through their owners. The Portal build consumes `portal_env`
from `bindings.json`; this renderer does not rebuild or deploy Portal.

`bindings.json` maps each Compose variable/file and pre-existing Kubernetes Secret
key to its Azure Key Vault reference. Provision those secrets through the owning
deployment process. File-backed Compose secrets must be readable by UID/GID 65532
without being world-readable; Compose bind mounts retain host ownership. Direct
environment secrets require the named variables in the private deployment process,
not a committed `.env` file. Never log resolved Compose configuration. Kubernetes
mounts use group 65532 and mode 0440. Preserve destination-specific receiver maps
and their other authorized producers.

Compose services use the explicit `production-payment` profile. Installing the
chart or starting that profile is a deployment action, separate from rendering.
Neither enables billing. Do not use the unit fixture as a deployment plan: its
images, database endpoints and identities are deliberately fabricated. The tests
compare the public settings, secret references and commands across targets.

Production synthetic checks and their declared fixtures belong to Infra. Keep
sandbox accounts, event payloads and test payment methods out of live Stripe.
No production charge, transfer or payout is part of configuration qualification.
