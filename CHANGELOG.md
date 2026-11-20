# Changelog

## Unreleased

- Use one versioned file journal for desktop, fonts, editors, shell and Git
  settings. Workspace recovery previews changes and preserves later edits.
- Render the selected font policy in the managed desktop applications.

## 0.2.0.dev2

- Added shared desktop font selection and a multilingual glyph sample.

## 0.2.0.dev0

- Add versioned `templates versions/create/update/cache` commands to the existing
  catalog, with reviewed annotated tags, immutable source commits and official
  Copier 9.17.2. Keep `templates list/path` and checkout resolution compatible.
- Support unattended answers, interactive choices and offline source preparation;
  refuse nonempty destinations, untrusted sources and unsupported refs. Copier
  tasks, dependency installation and Git commits remain explicit user operations.
- Add source-identified local CI receipts and canonical version/release checks;
  verify installed CLI metadata and bundled template choices outside the checkout.
