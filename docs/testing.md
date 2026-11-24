# Testing

Run the locked checks described in [development](development.md). Fixtures exercise validation, fixed Make goals, PTY authentication, signed-source failures, file races, recovery and package contents. Headless tests exercise the persistent tabs, preview, confirmation and cancellation.

## Disposable Debian target

Record the disposable target's identity and authorization, Debian release, CPU architecture, available filesystem space, encryption layout and current boot loader. Use synthetic accounts, content and secrets.

1. Install the stable Debian package on a clean system. Record its dpkg version and source field. Check which dependencies changed. Confirm that the UI did not start and the selected workstation software was not installed.
2. Change into an unrelated directory and start `setup-system` as the normal user. Save both sections. Capture the file hashes, modes, managed-file backup identities and a deliberately hand-edited file.
3. Reinstall the same package, then update from the preceding version. Confirm that selections, manual edits and backups survive. Repeat an update while Apply or Restore is active. The update must report a lock conflict.
4. Remove the configurator, check the preserved files and workstation applications, reinstall, and reopen both sections.
5. Exercise a representative native Apply, repeat to verify idempotence, then test recovery with a later user edit. Test the live compositor and connected monitor modes in an actual graphical session.
6. For existing LUKS only, verify the supported boot layout, backup, graphical prompt, text fallback and recovery menu on that target. Record a normal and a recovery boot.

Report package construction, publication, installation, graphical login and boot as separate results.
