# Login, clock and boot

An explicitly selected graphical-login stage configures greetd, gtkgreet and Cage.
The pre-authentication bridge/resources are system-owned. Native PAM authenticates the
normal desktop account, then the bridge starts Hyprland with its verified HOME/XDG
settings. No compositor or Python UI runs as root.

The next normal boot can use the configured graphical target. Application does not
switch sessions, reboot, stop SSH or enable autologin. Existing encrypted HOME must
already be unlocked before user runtime/configuration writes. Native disk unlock is
separate; see [graphical unlock](graphical_unlock.md).

An optional timezone is validated against installed zoneinfo. Existing locale, NTP
providers and network settings are preserved. The adapter records actual changes and
backs up managed login/GRUB files. GRUB retains a visible 5-second menu and a 10-second
recordfail recovery timeout. Existing boot entries and encryption parameters remain.

On a disposable target, check login, failed authentication, normal-user Hyprland,
Ctrl+Alt+F3 recovery and SSH before claiming success. Restore only unchanged managed
files from the receipt's backups; inspect `journalctl -u greetd` locally without
publishing account/session data. See [recovery](recovery.md).
