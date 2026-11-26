# Codex CLI settings reference

Generated from the plugin's Pydantic settings and action inputs.

## Settings

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `permission` | Set Codex permissions or inherit the existing choice. | `"inherit"` | `{"enum": ["inherit", "read-only", "workspace-write", ":read-only", ":workspace"]}` |
| `approval` | Choose when the Codex client requests approval for commands. | `"inherit"` | `{"enum": ["inherit", "on-request"]}` |
| `model` | Select a model reported by the installed Codex client. | `null` | `—` |
| `effort` | Select a reasoning effort supported by the active model. | `null` | `—` |
| `instructions` | Include the selected, digest-verified engineering instructions. | `false` | `—` |

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

## Register a reviewed HTTPS MCP server

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `project` | Leave empty for the current user's config. | `null` | `—` |
| `name` | Name the MCP server entry in the selected Codex configuration. | `required` | `{"maxLength": 64, "minLength": 1, "pattern": "^[a-z][a-z0-9_-]*$"}` |
| `url` | Enter the approved HTTPS endpoint for this MCP server. | `required` | `{"format": "uri", "maxLength": 2048, "minLength": 1}` |
| `enable` | Enable this server in the selected Codex configuration. | `false` | `—` |
