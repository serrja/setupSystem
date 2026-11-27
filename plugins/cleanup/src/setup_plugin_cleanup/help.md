# 🧩 Cleanup

Remove reviewed Debian packages safely.

## 🧹 Remove selected optional packages

Select individual candidates and review their installed versions and architectures. **Remove** retains Debian configuration files. **Purge** asks dpkg to delete the selected packages' configuration files. **Autoremove** simulates APT's unused dependency set and requires an exact match with your selected candidates. Each action has its own confirmation.

The plugin rejects held, essential, protected, partially configured and ambiguous packages. Removing nano or vim-tiny requires a surviving system editor and an independent backup editor. APT and dpkg are checked again after authentication. Package maintenance scripts may have effects outside the package file list. A removal has no automatic rollback.
