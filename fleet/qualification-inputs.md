# Fleet qualification inputs

There is no admitted fleet inventory yet. The workflow reports this and skips
qualification/publication. Do not fill this gap with fixture images or fabricated
provenance. Test publication is already authorized; missing runtime inputs are
not a human approval requirement.

A reviewed PR supplies `test.json`, `compose.json` and `chart.tgz` here.
`test.json` has exactly `schema` (integer 1), `components` (the bundle's digest,
embedded-version and source-revision map), and `assurance` (the existing provider
configuration). Compose/chart assets must implement the selected adapter contract,
match every image, preserve fail-closed behavior and contain no credentials.
Use the existing asset preparation commands when the images implement that profile;
arbitrary service images are not automatically compatible.

The qualifier assigns the bundle label from the main run ID, preserving all asset
bytes and embedded versions. PRs validate structure and rendered manifests only.
Main runs require `FLEET_TEST_CREDENTIALS`, a secret JSON object mapping each
selected assurance secret name to its test-only credential. Values are written
only to a temporary directory and the task-owned Kubernetes namespace, never the
uploaded bundle. Public images must be pullable from Docker and kind; private
image-pull credential integration remains separate work.

A `portal` component uses the Portal owner's qualified image from
`ghcr.io/hyperme-sh/controlplane-portal/runtime`. The main qualification workflow
reads the running Portal's signed-out footer in both engines and after Helm
upgrade. The footer must show the qualified image version and build and the exact
candidate tag resolved for that deployment. Configure the Portal workload to
serve its signed-out page on port 8080 in the disposable qualification job.

Before adding this inventory, record owner source/build provenance and dependency
qualification evidence for every image. This workflow checks immutable byte and
runtime receipt bindings; it does not independently attest image builds. Real
provider behavior, service-to-service smoke, persistent data, migration and
rollback acceptance must remain explicitly tracked. Compose health and Helm
readiness/upgrade alone do not establish those outcomes.
