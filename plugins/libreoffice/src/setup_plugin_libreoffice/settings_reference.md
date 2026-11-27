# ⚙️ LibreOffice settings reference

Generated from the plugin's Pydantic settings and action inputs.

## ⚙️ Settings

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `component` | Choose the LibreOffice component opened by this launcher. | `"start-center"` | `{"enum": ["start-center", "writer", "calc", "impress"]}` |

## 🧩 Install

This operation has no editable parameters.

## 🧩 Update

This operation has no editable parameters.

## 🧩 Uninstall

This operation has no editable parameters.

## 🧩 Check LibreOffice

This operation has no editable parameters.

## 🧩 Preferences

This operation has no editable parameters.

## 🧩 Recovery

This operation has no editable parameters.

## 🧩 Save application launcher

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `profile_id` | Stable identifier used in the desktop entry filename. | `required` | `{"pattern": "^[a-z][a-z0-9_-]{0,47}$"}` |
| `name` | Display name shown by the desktop application menu. | `required` | `{"maxLength": 80, "minLength": 1}` |
