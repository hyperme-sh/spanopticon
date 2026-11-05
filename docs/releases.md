# Coordinated releases

Every participating repository uses the same **major/minor** version. Patch
numbers advance independently: a `1.4` bundle can contain `1.4.2` and `1.4.9`.
A coordinated minor release advances every participating component to `1.5.x`.
A breaking API change requires a new major version, including before `1.0`.
Pre-release versions are not supported by this initial stable-release gate.

GitHub `test-<commit>` prereleases are development artifact snapshots. A wheel's
embedded package version does not publish or reserve that stable version.
Keep their artifact/consumer evidence under `releases/qualification`, outside
`releases/accepted` and `releases/candidate`. Qualification does not establish
stable API compatibility or a stable release baseline.

An accepted component record requires an actual immutable, non-draft,
non-prerelease GitHub release tagged `vMAJOR.MINOR.PATCH`. The shared component
workflow verifies that channel, the peeled tag's source commit and the published
asset digests against GitHub, both when accepting records and when consuming a
selected baseline. Candidate-supplied booleans are not evidence. Missing records
or unavailable verification fail stable admission closed. First stable release
bootstrap remains a separate explicit release operation; development snapshots
must not be installed as a substitute baseline.

A release bundle records the line, complete component membership, each component's
version, immutable artifact identity and digest-bound API snapshot. Keep private
component inventories and their snapshots in private release repositories. The
public examples contain invented components and artifact digests only.

```sh
python3 scripts/check_release.py \
  --previous /path/to/accepted/release.json \
  --candidate /path/to/candidate/release.json
```

Both files are required. The gate never silently substitutes the PR base or HEAD
for the last accepted release, and has no skip/waiver switch. Initial bootstrap
must be produced from tested artifacts, validated contracts and recorded provenance,
then recorded as an immutable accepted bundle before subsequent releases can use
this check. Bootstrap does not require a human approval step.

## Compatibility rules

- Patch releases must preserve the complete parsed API snapshot. Object-key order
  and whitespace may differ. OpenAPI `info.version` must match the component
  version and is excluded only from the compatibility comparison. Even additive API changes wait for a minor release.
- Existing versions cannot change their artifact or API. Versions cannot roll back.
- Component versions must match the bundle's major/minor. Membership changes wait
  for a coordinated minor; removal requires a major release.
- Minor releases preserve existing interfaces. The initial OpenAPI check admits
  wholly new non-overlapping paths and schemas while preserving all existing objects and other
  settings. Existing reference targets must remain identical. External/unresolved references,
  dynamic references and scope-changing schema identifiers are rejected when
  considering an additive minor change. Other unproven changes are rejected.
  Other snapshot formats must stay unchanged until an appropriate compatibility
  analyzer is integrated. This conservative gate can reject compatible changes.
- A major release permits breaking contract changes but still requires coherent
  component versions, complete snapshots and immutable artifact identities.

Export canonical snapshots without volatile build metadata. Include HTTP request
and response schemas, authentication requirements, events, protobuf descriptors,
configuration and public library signatures that consumers depend on. Do not
omit an interface just to pass the gate. An OpenAPI-only snapshot says nothing
about an omitted event or Python import contract.

The gate validates declared snapshots and artifact metadata offline; it does not
prove that an artifact exists, that it exports the snapshot, or that runtime
behavior is compatible. Each component's CI must build the exact artifact, export
its actual contracts, run its unit/provider/consumer tests and verify artifact
provenance. Breaking behavior with an unchanged schema must still fail those tests.

## Release pipeline

1. Build and test each component in its owning repository. Record its version,
   artifact digest, exported API snapshot and successful consumer qualification.
2. Assemble the complete candidate bundle for one release line. Pin every artifact;
   do not resolve `latest` or floating tags during promotion.
3. Retrieve the last accepted bundle from an immutable release revision,
   independently of candidate-controlled data. Run the policy gate and cross-service
   consumer tests. Require both checks before publishing/promoting a release.
4. Publish the accepted bundle and artifacts through the automated release
   workflow after the exact artifact and compatibility checks pass. Use a fixed
   baseline revision selected independently of candidate input.
5. Render deployment configuration from that accepted bundle. Verify registry
   provenance, environment configuration and rollback compatibility before applying.

This repository supplies the gate and reusable CI wiring. Adding the files does
not connect publication to validation in every component repository. Connect the
automated checks to each publisher, align versions and verify artifact provenance
before claiming pipeline enforcement. A manual image-only template is not a coordinated
release and must not be used to bypass the production promotion gate.


## Render a validated bundle

```sh
python3 scripts/render_release.py \
  --previous /path/to/accepted/release.json \
  --candidate /path/to/candidate/release.json \
  --output /path/to/new-output-directory
```

The renderer rejects incompatible bundles before writing files, pins container
images to validated digests and copies the digest-checked contracts. Package
artifacts remain in the lock manifest but do not create deployments. The output
must not exist. A final `release.json` is written only after all files are written.
No registry login, cluster operation, image pull or deployment occurs. Add reviewed
service-specific identity, configuration, storage and network overlays before
applying; generated services do not automatically gain access to one another.

A bundle has `schema_version: 1`, a `line` (`MAJOR.MINOR`) and a nonempty
`components` object. Each component names `version`, relative `contract`, raw-file
`contract_sha256` and `artifact` (`kind`, `uri`, `sha256`). Container URIs must carry
the identical digest. `kind: package` supports non-container library artifacts;
its digest/provenance must be verified by the artifact-producing workflow.
See the synthetic [example bundles](../examples/releases/README.md).


## Reuse the CI gate

Call `.github/workflows/release-policy.yml` from the private repository that owns
the assembled bundle. Pin the reusable workflow to its reviewed full commit SHA.
Supply `baseline-ref` as the full immutable commit of the **last accepted release**
in that repository, plus optional `baseline-manifest` and `candidate-manifest`
paths. The default paths are `releases/accepted/release.json` and
`releases/candidate/release.json`. The baseline and candidate need not have the
same path. Select the accepted commit from recorded successful release evidence,
independently of candidate input; a caller-controlled SHA alone is not acceptance.

The workflow reads only the caller repository and pinned public policy code. It
accepts no secrets, runs on hosted Ubuntu and never executes scripts from the
candidate bundle. Require its `compatibility` job together with artifact and
consumer tests as automated publication dependencies. Do not publish a release
merely because these metadata checks pass. No human approval is required by this
pipeline.

The public CI also calls this reusable workflow against the synthetic example
bundles and checks that its pinned policy code matches the reviewed source. When
changing policy code, update its immutable checkout pin in the same review; do
not leave reusable callers executing an older checker.

## Component repository rollout

The initial coordinated line is **0.24.x**. Earlier libraries already used minor
versions through 0.23, so 0.2 would be a downgrade. Do not rewrite existing package
versions or dependency pins merely to make the configuration check green.

Each owner keeps `release-policy.json` with `schema_version: 1`, its exact
`repository` and `component` name, `line: "0.24"`, and a nonempty `version_files`
list of its owning `pyproject.toml` or `package.json` files. Its caller workflow
pins `component-release.yml` by full SHA and passes the agreed line. Repository
variable `SPANOPTICON_RELEASE_BASELINE_SHA` must point to an immutable
commit containing `releases/accepted/release.json` and its real contracts/artifact
metadata. Submit the actual next bundle at `releases/candidate/release.json`.

A configuration-only PR check does not approve a release. A package version change
or candidate bundle submission requires full admission. Tag and manual release
requests always require admission and must match the candidate version. Missing
baselines fail closed. The candidate must contain exactly the owning component,
use the coordinated line and match every configured package version. The full
bundle policy still checks immutable artifacts, patch contracts and minor/major
compatibility. Artifact provenance and owning consumer tests remain separate
required checks; this workflow never publishes artifacts or deploys anything.

Publication must depend on successful automated compatibility, artifact and
consumer checks for the exact release. This is pipeline validation, not a GitHub
merge restriction or human approval prerequisite. Do not add required reviewers,
branch protections, approval rulesets, environment approvals, CODEOWNERS approval
requirements, or release/deployment approval gates unless the user explicitly
requests that specific change. This applies to production releases too. Advisory
reviews and automated tests remain welcome.

A workflow cannot prevent an authorized person from publishing outside that
workflow; describe enforcement as applying to the configured pipeline. GitHub
plan upgrades and branch protection are not adoption prerequisites. Real accepted
baselines and automated artifact/consumer qualification remain necessary.
