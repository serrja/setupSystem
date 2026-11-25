# LibreOffice settings reference

Generated from the plugin's Pydantic settings and action inputs.

## Settings

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `component` | Start component | `"start-center"` | `{"enum": ["start-center", "writer", "calc", "impress"]}` |

## Install

This operation has no editable parameters.

## Update

This operation has no editable parameters.

## Uninstall

This operation has no editable parameters.

## Check LibreOffice

This operation has no editable parameters.

## Preferences

This operation has no editable parameters.

## Recovery

This operation has no editable parameters.

## Save application launcher

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `profile_id` | Launcher profile ID | `required` | `{"pattern": "^[a-z][a-z0-9_-]{0,47}$"}` |
| `name` | Application menu label | `required` | `{"maxLength": 80, "minLength": 1}` |
