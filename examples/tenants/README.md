Render the example into a new directory with `python3 -m deployment.tenants examples/tenants/catalog.json /tmp/tenant-deployment`. The sample UUIDs and hosts are offline fixtures. Allocate IDs once in deployment configuration and reuse the same ID when joining a tenant. Configure peers with HTTPS URLs and base64-encoded 32-byte public keys.

Each portal uses its own configured login realm. Import its realm JSON into a fresh Keycloak deployment with the selected theme installed and referenced broker secret environment variables supplied. Keycloak resolves environment references during realm import. Set the matching portal environment from portal.json and supply the portal session secret separately. Leave login absent for a plane-only tenant. Leave a provider unconfigured to omit it from login. The route map is server-side. A configured peer does not grant a portal login or membership.

The plane.json files declare intended peers. Plane runtime adoption, key ownership proof and settlement policy remain separate qualification steps.

For peer configuration, declare a `plane` object on the tenant and each peer, e.g. `{"environment":"test","repository":"hyperme-sh/hypermesh-plane","version":"0.2.0","protocol_version":1}`. The renderer requires the same environment, repository and peer protocol version. For stable versions it requires the same major; for 0.x it requires the same major and minor. This catalog check discards incompatible declarations before network access. Protocol 1 is the proposed peering contract; declaring it does not attest that a running plane implements it.


For Plane #104's optional runtime listener, add `peer_listener` to a tenant:

```json
{"bind":"127.0.0.1","port":8443,"cert_file":"/run/secrets/peer.crt","key_file":"/run/secrets/peer.key","ca_file":"/run/secrets/peer-ca.crt"}
```

The renderer emits `peer-listener.json` beside `plane.json`. It contains schema
version 1, the tenant id, the listener fields and the sorted declared `peer_pins`.
Only absolute secret-file references are accepted; provisioning those mounted
files remains with the deployment owner. Private key contents are never rendered.
Plane consumes `HYPERMESH_PEER_PLANE_FILE` and `HYPERMESH_PEER_LISTENER_FILE` paths;
omit the listener declaration to keep the runtime listener off. Runtime must
compare tenant and pins against `plane.json` and fail closed on invalid TLS files.
Peer configuration creates no grants, enables no digest billing and changes no
service channel or package version. HTTPS peer origins cannot contain paths.
