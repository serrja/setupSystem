# Git author profiles

Workspace Tools reads the effective author and signing settings for an exact
checkout. The inspection includes their Git scope and origin. Signing key values
are hidden in the report.

To save an identity, choose a profile name and a source checkout with a configured
author name and email. An optional existing project directory can use this identity
through a native Git `includeIf` rule. Applying the profile to a repository sets its
local author fields. Git retains its credential providers and SSH configuration.

Configuration uses Git's own parser and writer. Existing conflicting profiles and
directory includes require review. Recovery previews the original files and checks
for edits made after the selected operation. [Recovery](recovery.md)
