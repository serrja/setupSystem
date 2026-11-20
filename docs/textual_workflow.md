# Textual workflow

Open `setup-system` as the desktop user. Ctrl+1 selects Base system; Ctrl+2 selects
Working environment. Both tabs remain available during execution. Tab and Shift+Tab
move focus; Enter activates the selected button. Settings are intentions, not observations.

Base system selects Hyprland, graphical login, keyboard, locale and timezone. Empty
timezone preserves the machine's setting. Workspace selects the desktop theme, connected display layouts, applications
and explicit cleanup candidates. Missing dependencies block application, not navigation
or saving intentions. A blocked selected feature can be deselected to apply independent
features.

Plan is read-only. Apply shows the complete immutable snapshot and requires confirmation.
Only then may sudo ask for its password using the terminal. Python does not receive or
store that password. Cancel and Quit wait until the current stage reaches a safe boundary;
completed stages remain applied and visible. Fix the reported condition and retry.

Workspace Tools provides the repository catalog, project selection, reviewed template
create/update, shell/RTK, Git inspection and backup restoration inside Textual. Templates
preserve reviewed versions and dirty working trees. No hidden public CLI is required.
See [recovery](recovery.md) for receipts and rollback limits.
