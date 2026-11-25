# Git settings reference

Generated from the plugin's Pydantic settings and action inputs.

## Settings

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `enterprise_host` | Enter the exact GitHub Enterprise DNS hostname you intend to use. | `""` | `{"maxLength": 253, "pattern": "^$\|^([a-z0-9]([a-z0-9-]*[a-z0-9])?\\.)+[a-z]([a-z0-9-]*[a-z0-9])?$"}` |

## Check Git

This operation has no editable parameters.

## Configure author profile

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `profile` | Profile name | `required` | `{"maxLength": 64, "minLength": 1, "pattern": "^[a-z][a-z0-9_-]*$"}` |
| `directory` | Apply this identity to repositories under this existing directory. | `required` | `{"format": "path"}` |
| `name` | Commit author | `required` | `{"maxLength": 128, "minLength": 1}` |
| `email` | Commit email | `required` | `{"maxLength": 254, "pattern": "^[^@\\s<>]+@[^@\\s<>]+$"}` |
| `editor` | Commit editor | `"nvim"` | `{"enum": ["nvim", "vim", "nano"]}` |
| `signing_key` | Full OpenPGP fingerprint. Leave empty for unsigned commits. | `""` | `{"pattern": "^([0-9A-Fa-f]{40}\|[0-9A-Fa-f]{64})?$"}` |

## Account status

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `host` | Git hosting service | `"github.com"` | `{"maxLength": 253, "minLength": 1}` |

## Sign in

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `host` | Git hosting service | `"github.com"` | `{"maxLength": 253, "minLength": 1}` |

## Sign out

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `host` | Host | `"github.com"` | `{"maxLength": 253, "minLength": 1}` |
| `account` | Account | `required` | `{"maxLength": 100, "minLength": 1, "pattern": "^[a-zA-Z0-9][a-zA-Z0-9_-]*$"}` |

## Create an SSH key

This operation has no editable parameters.

## Install

This operation has no editable parameters.

## Update

This operation has no editable parameters.

## Uninstall

This operation has no editable parameters.

## Upload public SSH key

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `host` | Host | `"github.com"` | `{"maxLength": 253, "minLength": 1}` |
| `account` | Account | `required` | `{"maxLength": 100, "minLength": 1, "pattern": "^[a-zA-Z0-9][a-zA-Z0-9_-]*$"}` |
| `public_key` | OpenSSH public key | `required` | `{"format": "path"}` |
| `title` | Title | `required` | `{"maxLength": 80, "minLength": 1}` |

## Revoke owned SSH key

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `host` | Host | `"github.com"` | `{"maxLength": 253, "minLength": 1}` |
| `account` | Account | `required` | `{"maxLength": 100, "minLength": 1, "pattern": "^[a-zA-Z0-9][a-zA-Z0-9_-]*$"}` |
| `key_id` | Remote key ID | `required` | `—` |

## Import repository identity

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `profile` | Profile name | `required` | `{"maxLength": 64, "minLength": 1, "pattern": "^[a-z][a-z0-9_-]*$"}` |
| `repository` | Checkout root | `required` | `{"format": "path"}` |

## Apply profile to checkout

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `profile` | Profile name | `required` | `{"maxLength": 64, "minLength": 1, "pattern": "^[a-z][a-z0-9_-]*$"}` |
| `repository` | Checkout root | `required` | `{"format": "path"}` |

## Revoke managed SSH keys

This operation has no editable parameters.

## Configure colors and aliases

This operation has no editable parameters.

## Verify SSH account access

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `host` | Host | `"github.com"` | `{"maxLength": 253, "minLength": 1}` |
| `account` | Account | `required` | `{"maxLength": 100, "minLength": 1, "pattern": "^[a-zA-Z0-9][a-zA-Z0-9_-]*$"}` |
| `public_key` | OpenSSH public key | `required` | `{"format": "path"}` |
| `login` | SSH transport user | `"git"` | `{"pattern": "^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$"}` |
