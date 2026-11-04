# Plane architecture

A service owns its API, persistence schema and migrations. Other services call its
public API or consume versioned events; they do not query its private tables or
import its application implementation. Shared libraries contain contracts rather
than hidden application state.

A plane configuration chooses immutable service artifacts, endpoint routing,
identity issuers and audiences, tenant policy, event transport and storage.
Keep these choices separate from service source and from operator credentials.

Before adding a service, record:

- Its owner, public API version and health/readiness endpoints.
- How callers authenticate and how tenant scope is verified.
- Its storage owner, migration ordering and backup/restore procedure.
- Its event schemas, retry behavior and idempotency boundary.
- Required configuration, credentials, resource limits and outbound access.
- Its rollback constraints and compatibility with other deployed versions.

Use explicit adapters for product-specific behavior. A reusable plane template
must not embed customer records, private environment inventories or product
business rules. A successful package test is separate from integration,
deployment and operational acceptance.
