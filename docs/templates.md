# Versioned project templates

`setup-system templates` extends the project catalog in
[projects.toml](../manifests/projects.toml). `python`, `typer`, `fastapi` and
`service` refer to reviewed sources; `templates path --name typerTemplate` keeps
its existing checkout lookup. Generation needs Python 3.12+, Git and the installed
setupSystem dependencies, independently of the Debian workstation installer.

## Select and create

```bash
setup-system templates list
setup-system templates versions fastapi
setup-system templates create --template fastapi --destination ../exampleApi
```

A terminal allows Copier's normal question prompts. Calling `templates create`
without options also asks for template, reviewed version and destination. In an
unattended shell, supply the alias and destination with `--defaults`; put required
answers in a JSON object and pass `--data answers.json`: `projectName` for
Python, Typer and FastAPI, or `service_name` for the service template. Python's
base template additionally requires its `profile`. `templates versions`
shows the exact accepted version spelling, annotated tag and full source commit.

The service alias stays visible even while its reviewed tagged release is
unavailable. It then reports `No approved tagged versions` and refuses generation;
reviewed Python, Typer and FastAPI versions remain usable independently.

```bash
setup-system templates create --template fastapi --version VERSION \
  --destination ../exampleApi --defaults --data answers.json
```

Replace `VERSION` with a listed value. The default is `recommendedVersion` from
the catalog, never `main`, `latest` or an arbitrary Git ref. A recommendation
change requires review of the exact annotated tag and commit. A missing approved
version fails before rendering. Nonempty destinations and symlink traversal are
rejected. No dependency installation, Git initialization, project commit, remote
creation or template task runs during generation. Unsafe Copier extensions and
migration hooks are not enabled.

The command prints the actual template version and source SHA, plus commands to
enter the project, create its lock when absent, install dependencies explicitly,
check, build and run it. An application starts with its own packaging version;
that number is independent of the source template version.

## Cache and offline generation

```bash
setup-system templates cache --template fastapi
setup-system templates cache --template fastapi --offline
setup-system templates create --template fastapi --destination ../offlineApi \
  --defaults --data answers.json --offline
```

Online preparation fetches only declared tags using the user's existing Git
access. Private sources require existing credentials; the command does not log in
or modify credential configuration. Each immutable catalog snapshot is cached
under `${XDG_CACHE_HOME:-$HOME/.cache}/setup-system/templates/`. Preparation is
locked and atomic. Every use verifies annotated tag type, exact commit, canonical template package version and Git
object integrity. An unavailable or damaged offline input is an error, never a
network fallback. Changing the catalog requires preparing its new snapshot.

Copier also maintains its normal working Git mirror. During the synchronous
Copier invocation, process-local Git URL rewriting points only to the verified
source cache and permits only the file protocol; no Git configuration file is
modified. The generated `_src_path` stays the canonical GitHub URL, so answers
remain portable. Source-cache availability does not imply an offline dependency
build: `uv.lock` inputs and Docker base/dependency caches must be prepared
separately.

## Reviewed updates

Review the generated project and deliberately initialize/commit it before its
first update. Preserve an external recovery copy when appropriate. Copier update
requires an exact, clean Git project and its genuine `.copier-answers.yml`.

```bash
setup-system templates versions fastapi
setup-system templates update --template fastapi --version VERSION \
  --destination ../exampleApi --defaults --offline
```

Both the recorded old version and chosen target must be in the trusted catalog
snapshot. The official [Copier update](https://copier.readthedocs.io/en/stable/updating/)
merges user changes using the recorded answers; setupSystem never rewrites those
answers manually. Tasks remain disabled. The generated template records its own
base-dependency provenance. Review settings, code, tests and migrations with the
resulting diff, then run the project's native checks. A merge conflict returns
exit 3 and remains in the working tree for review; other failures return nonzero.
No update commits the project or changes its remote.

## Dev source candidates

The following source snapshots have lint evidence only. The catalog's existing
recommendations remain unchanged; these new candidates are not silently selected
by generation or a stable channel. Full render/update acceptance was not run.

| Source | Candidate tag | Exact source commit |
| --- | --- | --- |
| pythonTemplate | v0.3.0.dev3 | 4c7e003099d7f2af2b86d4133ec4fe46e1863a4a |
| fastapiTemplate | v0.3.0.dev2 | cdd052c039682bd9782daab0fb0dcca17c7218ca |
| typerTemplate | v0.1.0.dev3 | 1080470759f689f0ded8eff3bc52c4c7ece8e7c1 |
| serviceTemplate | v0.3.0.dev2 | 62e43ff7438c855964b625535f3ebdac50b09ee3 |

Promoting a candidate into the trusted catalog is a separate reviewed update after
manual template acceptance. A published annotated tag alone is not that evidence.
