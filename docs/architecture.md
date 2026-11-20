# Architecture

`setup-system` and `python -m setup_system` open the same Textual application. There is
no public command router. The release bootstrap owns delivery only.

## Boundaries

| Boundary | Responsibility |
| --- | --- |
| `domain` | Immutable desired settings, plan snapshots, closed operation vocabulary, cleanup policy |
| `application` | Read-only plan, explicit consent, ordered stages, cancellation and workspace tools |
| `infrastructure` | TOML storage, downloads, user Ansible Runner, fixed privileged executor |
| `tui` | Persistent tabs, inputs, previews, confirmation and progress |
| `resources` | Versioned palette, catalogs and finite local roles |
| Existing Debian adapters | Signed APT acquisition, exact closure, service deferral and receipts |

The App owns the running service and worker. A tab never owns installation lifetime.
Only one mutation is accepted. The request is immutable; later UI changes belong to a
subsequent plan. Cancellation is checked between stages, never by killing dpkg.

## Privilege and source trust

The UI invokes a fixed stdlib program through sudo and the system interpreter. JSON on
stdin contains only a finite action and validated desired values. No user command,
playbook, module path or writable HOME source is accepted. The executor independently
retrieves release checksums and the native H02 build receipt, verifies source SHA and
archive contents, and imports only the freshly verified root-owned stdlib resources.
No pip, uv build hook, user Python plugin or user Ansible runs as root.

The reviewed APT adapter remains a narrow privileged implementation because replacing
its offline namespace, signed isolated sources, frozen closure, service deferral and
failure journal with arbitrary Ansible configuration would weaken these guarantees.
Ansible Core/Runner are locked and used only for finite user file roles; Jinja2 renders
the shared palette. Pydantic Settings validates desired state; TOML Kit preserves TOML
comments. This is a local application, not a second orchestration framework.

Published package/import names, native metadata, source/runtime delivery, provider
provenance, projectRules catalog and template version adapters remain compatible.
Engineering snapshots are pinned exclusively to `serrja/pythonTemplate` in
`.github/engineering-source.json`. Other template types remain catalog products.
