# setupSystem

Read selected [baseRules](https://raw.githubusercontent.com/serrja/baseRules/730418d3cc480b4f05b88b4c8275296c4845a75a/docs/engineering.md) before the local contract below.
These full-SHA links are reading sources, not automatic loaders or grants. Review
the source diff, update the explicit pin and run docs checks for a rules refresh.
Local overrides apply only to this repository's stated ownership boundary.

This repository owns Debian workstation installation and explicitly scoped, portable user configuration. The general package/OS installer stays Debian-only. `setup-system shell` may configure an already installed Zsh on the current Linux host and use `chsh` for the current user after registration in `/etc/shells`. Use English technical docs and concise headings; the practical Neovim guide is Russian. Follow `docs/naming.md` for new owned names and preserve external contracts. Keep `bootstrap.py` stdlib-only and reject unsupported platforms before any general install side effect. `plan` is strictly read-only. Preserve user fields and local data; back up before replacing managed files. Never source installation code from Zsh or Neovim startup, add a user to the Docker group, or promote a policy file into a sandbox claim. Technical and regression tests of changed mechanisms are expected. Run narrow syntax, type, lock and dry-run checks after edits.
