---
name: workspace-safety
description: Prepare a scoped local Codex workspace and verify file boundaries before changing a project; do not use for ordinary text answers.
---

# Scoped workspace

Input: an exact project root and task. Read its `AGENTS.md`, inspect Git state and select an available permission profile. Keep other projects and private data outside the writable scope. Use temporary sentinel files to test allowed and denied paths when enforcement can be observed. Output the actual boundary, residual risk and a short check record. A role file or `chmod` alone is not a sandbox; never claim unreadability without a tested deny rule.
