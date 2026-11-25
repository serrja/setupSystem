# Testing

Run `make native-ci` from the repository root with the locked toolchain. [Development](development.md)

## 🧪 What the native gate covers

Fixtures exercise validation, fixed Make goals, PTY authentication, signed-source failures, file races, recovery and package contents. Headless tests exercise the persistent tabs, preview, confirmation and cancellation.

## Disposable Debian target

Record the disposable target's identity and authorization, Debian release, CPU architecture, available filesystem space, encryption layout and current boot loader. Use synthetic accounts, content and secrets.

1. Install the stable Debian package on a clean system. Record its dpkg version and source field. Check which dependencies changed. Confirm that the UI did not start and the selected workstation software was not installed.
2. Change into an unrelated directory and start `setup-system` as the normal user. Activate two independent extensions, save their named profiles and inspect a program. Capture the file hashes, modes, managed-file backup identities and a deliberately hand-edited file.
3. Reinstall the same package, then update from the preceding version. Confirm that selections, manual edits and backups survive. Repeat an update while Apply or Restore is active. The update must report a lock conflict.
4. Remove the configurator, check the preserved files and workstation applications, reinstall, and reopen the plugin pages.
5. Exercise a representative native Apply, repeat to verify idempotence, then test recovery with a later user edit. Test the live compositor and connected monitor modes in an actual graphical session.
6. For existing LUKS only, verify the supported boot layout, backup, graphical prompt, text fallback and recovery menu on that target. Record a normal and a recovery boot.

Report package construction, publication, installation, graphical login and boot as separate results.

On that same target keep the rescue console available while testing root-authenticated package installation and the one-time sudo grant. Exercise a wrong password, a locked root account, a repeated grant and an account changed since preview. Verify unrelated groups and the current session before opening a fresh login and testing the user's own sudo password. At the same resolution and scale, compare the greeter and lock layout, failed and successful PAM challenges, and the identity after unlock. Record normal and recovery Plymouth boots separately.
