# Release guide

The release workflow calls the reviewed [H02 Python provider](https://github.com/pokerlost/.github/blob/e5cac33af228224af9deaef308e39c1b9f788033/docs/python_release_contract.md)
for allocation, independent native verification and GitHub Releases. The consumer
owns its native checks, build/injection and outside-installed acceptance. No package
registry publication or deployment is part of this workflow.

## 📦 Promotion and exact identity

1. Review the complete working-branch diff and exact SHA. Run `make native-ci` and include narrow regression evidence and honest
   installation/GUI limits in the PR.
2. Merge the lowerCamelCase working branch into `test` after actual required checks
   and review. Promote `test` → `dev` → `main` using ancestry-preserving merges.
   Preserve the permanent branches, configured approvals/checks and merge queue.
3. Check the CI run for the exact new dev/main push SHA. The `required` gate must
   pass before allocation; PR/test events do not allocate. Dev/main events have no
   path filters and are not cancelled by a newer push.
4. The provider allocates an immutable tag, the separate unprivileged native builder
   exports the exact source and builds the allocated PEP 440 version, and installed
   acceptance runs outside source. A signed receipt binds actual builder job/tools.
   The provider publishes the canonical Python release.
5. Read back the completed release, tag's peeled commit, native versions, source/run
   identity, checksums and attestations. Dev releases are prereleases and not latest;
   main releases are stable. Pending or queued is not released.

Before promotion, prevent repository-level automatic deletion of permanent head
branches. Do not rely only on omitting a CLI delete flag. Lack of branch-protection
administration is an administrative limitation; it does not permit bypassing actual
server checks or claiming an unknown protection state as verified.

## 🔎 Audit the published files

Set `release_tag`, `release_dir`, `artifact`, `source_sha` and `branch` to the
reviewed release, a new local directory, one published filename, the exact event
commit and `dev` or `main`. Resolve the annotated tag to its commit first; a tag
object SHA is not the source SHA. Compare the release manifest's allocation with
the original successful push run, including repository, branch, provider pin and
builder job identity.

```bash
gh release download "$release_tag" --repo serrja/setupSystem --dir "$release_dir"
(cd "$release_dir" && sha256sum --check checksums.sha256)
gh attestation verify "$release_dir/$artifact" --repo serrja/setupSystem \
  --source-digest "$source_sha" --source-ref "refs/heads/$branch" \
  --signer-workflow serrja/setupSystem/.github/workflows/ci.yml \
  --signer-digest "$source_sha" --deny-self-hosted-runners \
  --predicate-type https://slsa.dev/provenance/v1 --format json
```

Verify the wheel, sdist and `python-build-receipt.json` individually. The native
consumer builder signs these files, so its signer is this repository's `ci.yml`;
the reusable provider verifies and publishes them. Check the verified certificate's
source, signer and original run invocation, then compare its verified timestamp
with that original builder job's time window. Predicate fields alone do not prove
builder identity. Validate native archive versions and wheel RECORD hashes as well
as the checksums; none of these checks proves a working graphical installation.

Before a full original-run retry, retain the tag object, build-record tag, release
ID, published asset IDs/digests and original builder identity. After success, compare
all of them. An unchanged completed release with a skipped builder demonstrates
idempotent publication; it does not demonstrate recovery of an interrupted draft.

## 🔄 Recovery

Use a complete rerun of the **original** CI run, with its real numeric ID:

```bash
gh run view RUN_ID --repo serrja/setupSystem
gh run rerun RUN_ID --repo serrja/setupSystem
```

Rerunning only failed jobs is not the provider contract: the current attempt needs
fresh successful preflight. Before a verified build exists, recovery builds the same
source/version. After an immutable verified build record exists, allocation returns
its original artifact ID and skips rebuilding. Publication fills only missing matching
assets in an incomplete draft. A completed release is audited without replacement.
Conflicting tags, source identities, artifacts or proofs stop recovery; never use
force-update, clobber, a new dispatch event or a competing manual release.

Read-only reconciliation uses the exact pinned provider checkout and contract inputs
shown in its guide. Retain original Actions job records; an incomplete draft also
needs the original verified transport artifact. A local/mock retry is not hosted
interrupted-draft acceptance. Upstream H02 joint acceptance and this consumer's
actual run/release evidence must be reported separately; a reachable provider pin
alone proves neither.

APT and user configuration recovery are separate installer operations. A GitHub
release is not permission to rerun installation on the developer host or a claim of
transactional package rollback.

## Debian companion package

After the canonical stable release succeeds, this repository's Debian workflow builds the allocated version from the same main commit. The `deb-vX.Y.Z` companion release contains the Debian package, its SHA-256 checksum and the attestation bundle. The canonical `vX.Y.Z` release remains the default latest release. Install and update resolve that stable version once and verify the companion against its source commit and workflow identity before handing the bytes to APT.

Recovery resumes the original release run. A partial draft accepts only missing matching bytes. A conflicting artifact, different tag commit or absent attestation stops publication. Verify a published companion before documenting target installation results. [Debian workflow](https://github.com/serrja/setupSystem/blob/main/.github/workflows/debian.yml).
