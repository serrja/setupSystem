# Release guide

No merge, tag, package release or workstation apply is authorized by this change. Before an independently approved release, require source/static checks, full coverage with exact missing counts, clean wheel/sdist resource verification, supported Debian acceptance and backup/restore regressions. A red threshold remains a release blocker.

Review only task files and staged paths, scan for credentials and generated state, verify the exact source SHA and CI artifacts, and preserve the previous installed wheel and private backup. Apply only in the explicitly authorized platform and scope. Restore uses hash verification and dry-run by default; user conflicts and application databases require explicit recovery decisions. Never rerun relocation, shell configuration or grants as a delivery shortcut.
