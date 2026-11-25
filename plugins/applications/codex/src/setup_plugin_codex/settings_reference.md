# Codex CLI settings reference

Generated from the plugin's Pydantic settings and action inputs.

## Settings

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `permission` | Sandbox or named permission profile | `"inherit"` | `{"enum": ["inherit", "read-only", "workspace-write", ":read-only", ":workspace"]}` |
| `approval` | Approval policy | `"inherit"` | `{"enum": ["inherit", "on-request"]}` |
| `model` | Model | `null` | `—` |
| `effort` | Reasoning effort | `null` | `—` |
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

## Register a reviewed HTTPS MCP server

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `project` | Leave empty for the current user's config. | `null` | `—` |
| `name` | MCP server name | `required` | `{"maxLength": 64, "minLength": 1, "pattern": "^[a-z][a-z0-9_-]*$"}` |
| `url` | Reviewed HTTPS MCP endpoint | `required` | `{"format": "uri", "maxLength": 2048, "minLength": 1}` |
| `enable` | Enable this server for the selected scope | `false` | `—` |
