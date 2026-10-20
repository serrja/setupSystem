# Release guide

Review application compatibility and evidence before integrating or tagging a version.

1. Decide compatibility and update the one packaging version plus changelog.
   Refresh `uv.lock`; review CLI/config/generated-project compatibility and any
   required migration separately from workstation installation.
2. Run `make ci-local` and `make release-check`; review the exact content digest,
   artifact versions/checksums, staged paths and outgoing diff. Coverage below
   100% warns; missing or failed mandatory checks block delivery.
3. Open and review the PR, then merge only after its required checks succeed.
   A billing-blocked hosted check remains blocked even when local CI passes.
4. At the exact clean, checked commit on main, inspect tag-triggered workflows.
   Create one new annotated `v<version>` tag, push that exact ref and verify its
   remote tag object and peeled commit. Never move an existing tag or use a
   blanket `git push --tags`.
5. Publish a package/image, create a GitHub Release, deploy or apply workstation
   configuration only through a separately authorized workflow. Version tags
   alone do not authorize those actions. Current CI runs for main and PRs, not
   tag pushes; check that condition again immediately before a tag push.

Preserve the preceding installed wheel and private recovery backups. File restore
uses hash checks and dry-run by default; application databases and user conflicts
need separate recovery decisions. A release is never a reason to rerun relocation,
Zsh configuration, permissions or installation against an unsupported host.
