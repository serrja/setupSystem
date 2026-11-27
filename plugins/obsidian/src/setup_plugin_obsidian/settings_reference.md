# ⚙️ Obsidian settings reference

Generated from the plugin's Pydantic settings and action inputs.

## ⚙️ Settings

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `vault` | Select an existing vault to use for vault-specific actions. | `null` | `—` |

## 🧩 Install

This operation has no editable parameters.

## 🧩 Update

This operation has no editable parameters.

## 🧩 Uninstall

This operation has no editable parameters.

## 🧩 Check Obsidian

This operation has no editable parameters.

## 🧩 Create vault

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `path` | Choose the directory for the new vault inside your home. | `required` | `{"format": "path"}` |

## 🧩 Open vault

This operation has no editable parameters.

## 🧩 Select installed theme

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `name` | Choose an installed item from the selected vault. | `required` | `{"maxLength": 100, "minLength": 1, "pattern": "^[\\w .()-]+$"}` |

## 🧩 Configure an installed CSS snippet

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `name` | Choose a trusted snippet already present in the selected vault. | `required` | `{"maxLength": 100, "minLength": 1, "pattern": "^[\\w][\\w .()-]*$"}` |
| `enabled` | Set the selected feature or snippet to enabled or disabled. | `true` | `—` |

## 🧩 Change core feature

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `feature` | Choose the built-in vault feature whose setting will change. | `"bookmarks"` | `{"enum": ["bookmarks", "outline", "daily-notes", "templates"]}` |
| `enabled` | Set the selected feature or snippet to enabled or disabled. | `true` | `—` |

## 🧩 Keyboard shortcuts

This operation has no editable parameters.

## 🧩 Editor settings

This operation has no editable parameters.

## 🧩 Account and Sync

This operation has no editable parameters.

## 🧩 Community plugins

This operation has no editable parameters.

## 🧩 Inspect command shortcut

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `command_id` | Choose a command reported by the currently open vault. | `required` | `{"pattern": "^[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,127}$"}` |
