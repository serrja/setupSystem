# Obsidian settings reference

Generated from the plugin's Pydantic settings and action inputs.

## Settings

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `vault` | Selected vault | `null` | `—` |

## Install

This operation has no editable parameters.

## Update

This operation has no editable parameters.

## Uninstall

This operation has no editable parameters.

## Check Obsidian

This operation has no editable parameters.

## Create vault

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `path` | New vault directory | `required` | `{"format": "path"}` |

## Open vault

This operation has no editable parameters.

## Select installed theme

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `name` | Installed theme | `required` | `{"maxLength": 100, "minLength": 1, "pattern": "^[\\w .()-]+$"}` |

## Change core feature

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `feature` | Feature | `"bookmarks"` | `{"enum": ["bookmarks", "outline", "daily-notes", "templates"]}` |
| `enabled` | Enabled | `true` | `—` |

## Keyboard shortcuts

This operation has no editable parameters.

## Editor settings

This operation has no editable parameters.

## Account and Sync

This operation has no editable parameters.

## Community plugins

This operation has no editable parameters.

## Inspect command shortcut

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `command_id` | Command | `required` | `{"pattern": "^[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,127}$"}` |
