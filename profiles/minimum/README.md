# Minimum operator bundle

Build from the public Spanopticon checkout. The local test issuer signs short-lived access tokens for two different tenants. The reference service exposes a versioned tenant resource, checks the signature, issuer, audience and tenant, and stores provisioned tenant IDs in SQLite. Its startup migration creates schema version 1 and refuses unknown versions. The standard-library Python client in `client.py` calls `GET /v1/tenants/{tenant_id}`.

From the repository root on Linux with Docker Compose, Python 3 and OpenSSL:

```sh
python3 profiles/minimum/setup.py /tmp/minimum-config
docker compose --env-file /tmp/minimum-config/compose.env -f profiles/minimum/compose.yaml --profile local-issuer up --build -d --wait
```

Use `docker compose` with the same options and `port api 8000` or `port identity 8000` to find the loopback endpoints. Run `python3 profiles/minimum/check.py --api http://HOST:PORT --issuer http://HOST:PORT --accounts /tmp/minimum-config/clients.json` with those two respective endpoints. The check signs in both accounts, reads the owner's resource and requires HTTP 403 for the other tenant. Keep the generated configuration directory private. Stop the stack with the same Compose options and `down`. The named volume preserves directory state.

For a disposable end-to-end installation run `bash profiles/minimum/qualify.sh compose` or `bash profiles/minimum/qualify.sh helm`. Helm qualification needs kind, kubectl, Helm and jq. It builds the same Dockerfile, loads the image into a fresh cluster, installs the chart with a PVC, runs both tenant checks and removes the cluster. The checked-in workflow installs checksum-pinned Kubernetes tools. For a persistent Helm install, make the image available to the cluster, create a Secret containing `issuer.pub` and set `image`, `tenant` and `existingSecret` on the chart.

**External issuer.** Start only the API (omit the Compose profile or keep Helm's localIssuer false). Supply the trusted PEM verification key in issuer.pub, a tenant UUID, exact issuer and audience, and EdDSA or RS256. Tokens require sub, tenant_id, exp and iat. Configure these with the IDENTITY_* environment settings in Compose or the issuer, audience and algorithm Helm values. Key rotation requires replacing the mounted key and restarting the API. Use TLS for external traffic. The bundled test issuer uses its own local login endpoint.

**Dependency inventory.** The Dockerfile pins the public Python base image by digest. The operator requirements.lock pins transitive PyPI artifacts by hash, including FastAPI, Uvicorn, PyJWT, cryptography and their dependencies. The SQLite runtime comes from the base image. This profile reuses the public local issuer and hash lock; some dependencies in that shared lock are only used by the larger operator sample. Source, protocol, SDK, configuration, migration and deployment assets all live in this repository. Pin the checkout commit and run `sha256sum --check profiles/minimum/deployment.sha256` from the checkout root to verify the locked source, build inputs and manifests. Both CI engines check the lock before installation. Cluster tooling pins live in scripts/install_deployment_test_tools.py and qualify.sh. Docker Engine, Compose, jq, OpenSSL and the host Python are operator prerequisites.
