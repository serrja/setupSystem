# ⚙️ Administration settings reference

Generated from the plugin's Pydantic settings and action inputs.

## ⚙️ Settings

This operation has no editable parameters.

## 🧩 Grant sudo access

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `username` | An ordinary account in the local passwd and group databases. | `required` | `{"maxLength": 32, "minLength": 1, "pattern": "^[a-z_][a-z0-9_-]{0,31}$"}` |

## 🧩 Verify effective access

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `username` | An ordinary account in the local passwd and group databases. | `required` | `{"maxLength": 32, "minLength": 1, "pattern": "^[a-z_][a-z0-9_-]{0,31}$"}` |
