---
name: ci-docker
description: Build a repository-specific CI or Docker test environment without deployment or new business tests; use when automation is explicitly requested.
---

# CI and Docker environment

Input: current commands, tests and build toolchain. Pin actions to verified full SHAs, use least GitHub permissions and honest no-tests status. Keep Docker resources isolated and never prune unrelated state. Output workflow, checked commands and blocked credentials/targets. Package publication and production deploy need separate authorization.
