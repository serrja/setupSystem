# Codex CLI settings reference

Generated from the plugin's Pydantic settings and action inputs.

## Settings

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `permission` | Sandbox mode | `"workspace-write"` | `{"enum": ["read-only", "workspace-write"]}` |
| `approval` | Approval policy | `"on-request"` | `{"enum": ["on-request", "untrusted"]}` |
| `model` | Model | `""` | `{"maxLength": 160}` |
| `effort` | Reasoning effort | `""` | `{"maxLength": 64}` |
| `instructions` | Include the pinned engineering instructions | `false` | `—` |

## Check Codex

This operation has no editable parameters.

## Install official CLI

This operation has no editable parameters.

## Update official CLI

This operation has no editable parameters.

## Configure client

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `project` | Leave empty for the current user's config. | `null` | `—` |

## Sign in

This operation has no editable parameters.

## Account status

This operation has no editable parameters.

## Sign out

This operation has no editable parameters.

## Manage MCP servers

This operation has no editable parameters.

## Manage skills

This operation has no editable parameters.

## Uninstall managed CLI

This operation has no editable parameters.
