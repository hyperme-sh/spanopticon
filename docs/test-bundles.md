# Test bundles and gold promotion

The former Test bundles workflow that published `0.24.0-test.N` GitHub
prereleases, and the gold-promotion dispatch that copied a selected test
archive to a stable bundle, have been removed. Automatic package or template
prereleases are not published.

Main `Validate templates` checks still run. They do not publish prerelease
assets. Fleet test container tags remain a separate path, documented in
[fleet test publication](fleet-test-publication.md).

Stable promotion, when used, still requires explicit selection of a candidate
and destination. Component package admission remains separate from any
historical test-bundle label. Python package versions are not renamed to
`test.N`.
