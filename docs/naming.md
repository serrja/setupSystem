# Naming policy

This is the canonical naming rule for new work owned by these repositories. Use short lower camelCase for new multiword project names, directories, files, internal variables, methods, configuration fields, and explicitly named CLI commands. Use PascalCase for types and UPPER_SNAKE_CASE for constants and environment contracts. Write headings and prose in natural language. Examples: `projectTemplates`, `terminalDev`, `keyMaps.lua`, `projectRoot`, `resolveProjectRoot`, `apiClient`, and `projectId`.

Python style here is an explicit owner preference, not a claim about PEP 8. Preserve external names: dunder methods, protocol overrides, published imports/distributions, wire and SQL fields, existing CLI names, environment variables, and tool discovery paths. `README.md`, `AGENTS.md`, `SKILL.md`, `pyproject.toml`, `uv.lock`, `init.lua`, `lazy-lock.json`, `CMakeLists.txt`, `Dockerfile`, `.github/workflows`, and `.agents/skills` keep their required names. Vendor code and personal notes are outside naming migrations.

## Rename map

| Old name | New name | References and check |
| --- | --- | --- |
| Earlier `project-templates` candidate | `projectTemplates` | Local folder and GitHub repository use the new name; four profile render/build checks passed. |
| Current `projectTemplates` checkout | `pythonTemplate` logical/local target | The existing remote remains `serrja/projectTemplates`; the directory move and template split are pending. |
| Copier `project_name` answer | `projectName` | `copier.yml`, every Jinja file and generated answers; four renders. |
| Copier `package_name` derived answer | `packageName` | Template path, package metadata, entrypoints, docs, CI; four renders/builds. |
| New vault section labels without a path convention | `самоОбучение`, `моиПроекты`, `повседневныеДела` paths | `Главная.md`, `project.json`, section links; full-path link check. Existing study paths stay unchanged. |

Existing repo names and public contracts keep their current spelling. Internal changes in an existing project require a separate review of imports, exports, tools, CI, links and case-insensitive filesystem collisions, followed by that project's own checks. A cosmetic rename never changes business behavior. Do not create a permanent alias layer for a private internal name.
