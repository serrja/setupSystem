# Git author profiles

Workspace → Tools provides git_profiles, git_inspect, git_configure, git_use and
git_restore. These configure author/signing identity, not account authentication.
SSH keys, credential helpers, tokens and account stores remain with Git/gh.

For git_configure, supply a profile Name, an exact source repository in Destination and
an explicit JSON array of existing owner directories in Data. The use case copies only
confirmed author/signing fields and creates native `includeIf` defaults. Review before
confirming. Existing conflicting profiles/includes are preserved.

For git_use, select the profile Name and exact repository Destination. Only local author
fields change; branches, remotes and global defaults remain. git_inspect shows effective
scope/origin so environment overrides and precedence remain visible. Repository identity
is independent from GitHub authentication and push authorization.

git_restore uses the Backup ID and refuses later edits. Backups stay private under user
state. Never publish real author information or credential output in a support issue.
