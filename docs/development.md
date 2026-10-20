# Development

Use this exact repository root with Git, Make, Docker and Buildx. The Dockerfile
pins Python and uv; the lock owns application and development dependencies.

```bash
make help
make version
make ci-local
make coverage
make release-check
```

`ci-local` runs the applicable check, build and installed-wheel verification graph
without repeating the source suite. Reports live in ignored `coverage/` and
identify the source commit, dirty state, content digest, tool versions, commands,
exit codes and artifacts. Coverage below 100% is a warning; failed assertions,
types, builds and missing or stale reports remain errors. Hosted CI invokes the
same Make entrypoint. Local checks do not require Actions credentials or caches.

For a fast edit loop, use `uv sync --locked`, `uv run --locked ruff check .`,
`uv run --locked mypy` and the relevant `uv run --locked pytest tests/...` case.
These are development checks, not evidence that the full Docker graph passed.
The first dependency/image fetch may need network access. After a successful
`make ci-local` for the same source, `OFFLINE=1 make ci-local` verifies prepared
quality and installed image identities plus artifact hashes. It reuses those
artifacts and runs source checks and installed CLI smoke again with no network
or Docker rebuild. Missing, replaced or changed inputs are blocked.

The source suite operates on synthetic homes and temporary Git projects. It does
not install host configuration or exercise Debian GUI behavior. Read
[testing](testing.md), [versioning](versioning.md) and [release](release_guide.md)
for the boundaries of each result. The managed release helper keeps its owner’s 88-column formatting, checked separately
from this repository’s 100-column application code. The shared Docker recipe belongs to
pythonTemplate and is refreshed from a reviewed source; do not hand-edit the
vendored copy to create a second implementation.
