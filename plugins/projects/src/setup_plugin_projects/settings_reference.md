# ⚙️ Projects and templates settings reference

Generated from the plugin's Pydantic settings and action inputs.

## ⚙️ Settings

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `offline` | Use template releases that have already been downloaded. | `false` | `—` |

## 🧩 inspect projects

This operation has no editable parameters.

## 🧩 prepare directories

This operation has no editable parameters.

## 🧩 template cache

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `template` | Select a template from the packaged project catalog. | `required` | `{"maxLength": 32, "pattern": "^[a-z][a-z0-9]+$"}` |
| `version` | Leave empty to resolve the latest stable annotated release. | `""` | `{"maxLength": 80, "pattern": "^[a-zA-Z0-9.+-]*$"}` |

## 🧩 template create

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `template` | Select a template from the packaged project catalog. | `required` | `{"maxLength": 32, "pattern": "^[a-z][a-z0-9]+$"}` |
| `version` | Leave empty to resolve the latest stable annotated release. | `""` | `{"maxLength": 80, "pattern": "^[a-zA-Z0-9.+-]*$"}` |
| `destination` | A directory inside your HOME. | `required` | `{"format": "path"}` |
| `project_name` | Name the project directory that will be created. | `required` | `{"pattern": "^[A-Za-z][A-Za-z0-9_]{0,79}$"}` |
| `python_profile` | Choose the project type expected by the upstream Python template. | `null` | `—` |

## 🧩 template update

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `template` | Select a template from the packaged project catalog. | `required` | `{"maxLength": 32, "pattern": "^[a-z][a-z0-9]+$"}` |
| `version` | Leave empty to resolve the latest stable annotated release. | `""` | `{"maxLength": 80, "pattern": "^[a-zA-Z0-9.+-]*$"}` |
| `destination` | A directory inside your HOME. | `required` | `{"format": "path"}` |

## 🧩 Open in Neovim

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `project` | Choose the managed checkout that this action will inspect or update. | `required` | `{"maxLength": 128, "pattern": "^[a-z][A-Za-z0-9]*/(?:[A-Za-z][A-Za-z0-9]*\|\\.github)$"}` |

## 🧩 Open in Codex

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `project` | Choose the managed checkout that this action will inspect or update. | `required` | `{"maxLength": 128, "pattern": "^[a-z][A-Za-z0-9]*/(?:[A-Za-z][A-Za-z0-9]*\|\\.github)$"}` |

## 🧩 List template releases

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `template` | Select a template from the packaged project catalog. | `required` | `{"maxLength": 32, "pattern": "^[a-z][a-z0-9]+$"}` |
