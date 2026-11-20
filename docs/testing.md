# Testing

`make native-check` runs the locked Ruff/format/type, ShellCheck, Ansible lint/syntax,
entrypoint and fixture gates. `make native-ci` adds wheel/sdist and outside-installed
acceptance. [Development](development.md) owns the exact commands and runtime versions.

Tests use synthetic HOME/XDG, temporary Git fixtures, mock package/system executors,
private APT simulations and headless/PTY Textual. They check source checksums/provenance,
caller validation, unsafe archives, screen changes during apply, immutable snapshots,
consent, cancellation, partial failures, recovery, conflicts and removal-negative cases.
Direct transport, release provenance, Copier updates and Debian adapter checks also run in the native suite.

No test applies the system installer on the developer host. Container/native results
are not physical installation evidence. Real boot, LUKS unlock, GPU/Hyprland, panel,
audio, portals, Firefox policies/extensions and ChatGPT/Happ GUI acceptance require an
explicitly selected disposable Debian target. Report unavailable checks honestly.


## Disposable Debian acceptance

Use a disposable Debian 13 installation with a named owner, console access, a recoverable snapshot and synthetic user data. Record the architecture, kernel, GPU, connected outputs, bootloader, release tag, peeled commit and release checksum before starting. Keep the previous boot entry available. Repeat on each architecture for which target verification is claimed.

| Stage | Exercise | Evidence |
| --- | --- | --- |
| Baseline | Open Textual as the normal user. Navigate both sections, preview timezone and a Firefox-only plan, then quit. | Machine facts, actions, unchanged managed-file hashes and absence of a sudo prompt. |
| Regional settings | Confirm one supported timezone, locale or keyboard change, then plan it again. | Native readback and No changes for the repeated selection. |
| Desktop | Select Hyprland and graphical login. Switch tabs during Apply. Log into the session from the greeter using the synthetic account. | Session and portal journals, audio playback and recording, polkit prompt, lock and unlock, keyboard switching, Waybar and Foot. |
| Displays and power | Preview one advertised mode and let it time out. Select a verified layout and exercise connected displays and present power devices. | Actual output, mode, scale and positions before, during and after the preview. Verify panel modules match detected hardware. |
| Editor and apps | Confirm each selected profile. Open a fixture source file, format it and copy to the Wayland clipboard. Launch the selected browsers and applications. | Native versions, initialized language server, formatted content, clipboard round trip, Firefox policies and signed extension. Record account login separately. |
| Recovery | Change an owned file, create another, then edit one manually and preview Restore. Repeat with an unchanged operation and restore it twice. | Protected later edit, restored bytes and modes, removal of created paths and repeat result. |
| Cleanup | Preview one reviewed candidate with installed dependencies. Change the package state between confirmation and Apply on the disposable snapshot. | Approved closure, protected-package and hold handling, refusal on changed closure. |
| Encrypted boot | Follow the graphical unlock preflight and reboot the authorized target manually. | Kernel, regenerated image, graphical and text prompts, successful manual unlock, fallback and recovery from the snapshot. |

Keep user secrets, passwords, subscription links and real account contents out of evidence. [Graphical unlock procedure](graphical_unlock.md)
