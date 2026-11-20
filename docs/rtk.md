# RTK

Workspace Tools installs or updates the optional RTK output filter from its
reviewed release. Apply checks the archive checksum and tests the staged
executable's version and command identity before publishing the binary.

Run `rtk --version` and `rtk gain --help` to inspect the installed tool. Use the
underlying command, such as `git diff --check`, when you need its full output.
Recovery can restore the previous binary while its recorded hash and permissions
still match. [Recovery](recovery.md)
