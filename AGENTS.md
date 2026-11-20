# setupSystem

Read the pinned [baseRules engineering contract](https://github.com/serrja/baseRules/blob/8434509933feaa71ba312f8d02caad7362766d5a/docs/engineering.md)
and [policy](https://github.com/serrja/baseRules/blob/8434509933feaa71ba312f8d02caad7362766d5a/docs/policy.md), then this local contract.
Reviewed full-SHA links are reading sources, not automatic loaders or permission grants.

This repository owns Debian workstation preparation, Hyprland installation and explicitly
scoped portable user configuration. General system installation remains Debian-only;
profile capabilities can be narrower. Detect and reject unsupported targets before side
effects. Keep the source handoff and privileged installation service stdlib-only,
and planning strictly read-only. Textual is the only public configuration interface.
Do not restore the retired system shell installer or public command router.
The release bootstrap delivers the app and launches it as the normal user.

Preserve projectRules in the catalog, the selected editor profile, user data and local edits. Back up
managed files before replacement and reject unknown conflicts. Never run installation
from shell/editor startup, add Docker-group membership, replace the system Python, or
infer sandbox isolation from a policy file. Separate root system operations from verified
user HOME/XDG operations. Never apply the installer to a developer host as a test.

Use English public engineering prose/comments and follow [naming](docs/naming.md).
Run the changed area's regression tests, locked Ruff/format/type checks and the applicable
native gate. [Development](docs/development.md) owns commands and evidence limits.
Branches follow a lowerCamelCase working branch → `test` → `dev` → `main`; preserve
permanent branches and ancestry. CI releases each successful dev/main push at its exact
source SHA through the pinned shared provider. See [release](docs/release_guide.md).
