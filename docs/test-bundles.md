# Test bundles and gold promotion

`.github/test-channel.json` declares the next bundle version (`0.24.0`) and the
validation workflow that owns its artifacts. Changing the target is a deliberate
source change. The initial declaration does not advance any embedded service
package version, dependency constraint or accepted API baseline.

A successful `Validate templates` main-push run retains one `tested-dist` artifact.
The separate publisher runs trusted, pinned shared code without checking out or
executing the caller's source. It verifies the successful run's repository, source
commit, main branch and workflow, then checks GitHub's artifact ZIP digest. The
source's channel declaration must agree with current main.

The publisher allocates `0.24.0-test.N` atomically, starting with zero. It publishes
the unchanged archive and a manifest containing the source revision, validation
run, original artifact identity and asset digests as a GitHub prerelease. There is
no floating `test` tag and no package-index upload or infrastructure deployment.
Publication order defines N; cross-repository deployment must select exact
component bundles and digests, rather than assume matching N means compatibility.

To promote a qualified gold candidate, the owner explicitly selects an exact
`gold_test`, such as `0.24.0-test.7`, in the **Test bundles** workflow on main. The
publisher verifies the original immutable prerelease and Actions artifact, then
copies all assets without rebuilding to the declared stable bundle `0.24.0`.
The original test manifest remains byte-identical and records where those bytes
were qualified. The destination release/tag expresses their stable selection.
An agent must obtain explicit authorization for that candidate and destination
before dispatching promotion. No automatic stable selection or promotion occurs.

Existing immutable-release settings remain in place. No reviewer, branch
protection or environment approval is introduced. Successful retries verify
existing bytes; incomplete drafts or mismatched tags stop for inspection and are
never overwritten or deleted automatically. Original Actions artifacts must be
available during promotion (90-day requested retention, subject to repository
retention). Missing or expired evidence requires fresh qualification.

The former template `vMAJOR.MINOR.PATCH` publisher is removed; this repository
uses the explicit gold-selection path for stable bundles. Component repositories
with a `release-policy.json` may publish test bundles but cannot promote them as
stable bundles: their package admission remains separate. A test bundle must not
be recorded as an accepted component-package baseline. Python package
versions are not renamed to `test.N`, and promoting a bundle does not relabel wheel
metadata. This preserves the exact dependencies and bytes that were tested.
