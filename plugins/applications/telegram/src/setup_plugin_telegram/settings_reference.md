# Telegram Desktop settings reference

Generated from the plugin's Pydantic settings and action inputs.

## Settings

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `startup` | Launch behavior | `"normal"` | `{"enum": ["normal", "tray", "settings"]}` |

## Install

This operation has no editable parameters.

## Update

This operation has no editable parameters.

## Uninstall

This operation has no editable parameters.

## Check Telegram Desktop

This operation has no editable parameters.

## Accounts

This operation has no editable parameters.

## Notifications

This operation has no editable parameters.

## Save application launcher

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `profile_id` | Launcher profile ID | `required` | `{"pattern": "^[a-z][a-z0-9_-]{0,47}$"}` |
| `name` | Application menu label | `required` | `{"maxLength": 80, "minLength": 1}` |

## Remove previously managed Debian package

This operation has no editable parameters.
