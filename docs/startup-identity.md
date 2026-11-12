# Configured startup reconciliation

The startup command consumes Infra's private deployment envelope from #882:
its canonical `binding`, verified `binding_sha256`, and separate `owner`
profile, tenant/network and broker metadata. The envelope selects an existing
Entra account and SEA public key. It contains no SEA private key. The command
never chooses an account by email or creates an external identity.

`python -m startup.command --config FILE --state-dir DIRECTORY` validates the
complete mapping, every owner endpoint and four distinct private credentials
before contacting User, Auth, ACL, then Keycloak. Each owner verifies the same
mapping fingerprint. State files are owner-private and atomic; an exclusive
lock serializes concurrent invocations. Retry rechecks all earlier owners,
including after a peer committed its write but the local checkpoint was lost.
A changed person, scope, grant inventory or mapping stops the sequence.

The normal deployment entry point is `python -m startup.bootstrap --input-dir
DIRECTORY --state-dir DIRECTORY`. It stages read-only secret-mount inputs into
owned mode-0600 files and invokes that command. `startup/compose.yaml` adds the
one-shot command after the independently deployed owners are ready and their
explicit owner migrations are installed. It is disabled by default. Mount an
existing private input directory containing `mapping.json`, `user.token`,
`auth.token`, `acl.token`, and `keycloak.token`; `entra-client.secret` is needed
only to configure a missing dedicated Entra broker. The input and state paths
must be accessible to UID 65532. Runtime readers refuse public, multiply linked
or symlinked files. Shared TEST data is never reset by this command.

Build the deployment image from `startup/Dockerfile`, supplying the unpacked,
verified controlplane-keycloak tooling archive as the named `keycloak-owner`
build context. Install that archive's provider JAR in Keycloak before enabling
startup. The image contains the native owner command; it does not fetch tooling
at runtime. Deployment follows a moving TEST image channel with always-pull
behavior. Recorded artifact digests identify downloads; they do not pin peers.

The orchestrator receives the User reconciliation credential, Auth reconciliation
credential, ACL reconciliation credential and Keycloak administrator credential
through separate files. User/Auth/ACL retain their own narrower reader/binder
credentials. Only the Keycloak credential and optional configured Entra application
secret reach the Keycloak child process. Browser credentials cannot call any
reconciliation route. Templates contain paths, references and defaults, never
account IDs, keys or credential values. Existing admin flags are independent:
organization ownership does not imply platform or Keycloak administration.

Startup remains a deployment operation; this change does not run it against TEST
or authorize publication, version changes or production promotion. An existing
broker alias with the wrong provider type is a conflict, not an instruction to
replace it. Follow the Keycloak owner's migration instructions for that case.

## Scheduled qualification

Infra #923 owns registration, private fixture staging, readiness and cleanup.
`python -m startup.nightly --fixture FILE --output DIRECTORY` runs only in the
scheduled Infra main batch. It uses the declared isolated owner fixture and a
fresh verified broker login token. It interrupts after Auth commits, retries the
same boot, repeats boot, resolves the same SEA identity, requests each declared
organization session, and checks both a foreign organization and misuse of one
company's scoped session against another company. It uses actual HTTP routes;
there is no mocked successful path in that command. Missing owner deployment,
private binding, scoped credentials or fresh login token blocks the fixture.
Cleanup removes run-owned local files only and preserves existing TEST data.
Unit tests exercise its protocol using explicit in-memory doubles; those results
are not substituted for the scheduled command.

Prerequisites: User #94, Auth #51, ACL #73, Keycloak #32, shared identity #32,
and Infra #941. Their independently updated contracts remain authoritative.
