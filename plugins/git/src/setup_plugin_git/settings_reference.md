# Git settings reference

Generated from the plugin's Pydantic settings and action inputs.

## Settings

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `enterprise_host` | Enter the exact GitHub Enterprise DNS hostname you intend to use. | `""` | `{"maxLength": 253, "pattern": "^$\|^([a-z0-9]([a-z0-9-]*[a-z0-9])?\\.)+[a-z]([a-z0-9-]*[a-z0-9])?$"}` |

## Check Git

This operation has no editable parameters.

## Configure author identity

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `identity` | Name the local Git author identity used for this repository selection. | `required` | `{"maxLength": 64, "minLength": 1, "pattern": "^[a-z][a-z0-9_-]*$"}` |
| `directory` | Apply this identity to repositories under this existing directory. | `required` | `{"format": "path"}` |
| `name` | Set the author name recorded in new Git commits. | `required` | `{"maxLength": 128, "minLength": 1}` |
| `email` | Use this email address as the author address for commits. | `required` | `{"maxLength": 254, "pattern": "^[^@\\s<>]+@[^@\\s<>]+$"}` |
| `editor` | Open commit messages with the selected editor. | `"nvim"` | `{"enum": ["nvim", "vim", "nano"]}` |
| `signing_key` | Full OpenPGP fingerprint. Leave empty for unsigned commits. | `""` | `{"pattern": "^([0-9A-Fa-f]{40}\|[0-9A-Fa-f]{64})?$"}` |

## Create an SSH key

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `name` | A new private directory under ~/.ssh/setup-system contains the pair. | `required` | `{"pattern": "^[a-z][a-z0-9_-]{0,47}$"}` |

## Install

This operation has no editable parameters.

## Update

This operation has no editable parameters.

## Uninstall

This operation has no editable parameters.

## Upload public SSH key

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `host` | Choose the hosting service for this account operation. | `"github.com"` | `{"maxLength": 253, "minLength": 1}` |
| `account` | Select the account whose session or resources this action will inspect. | `required` | `{"maxLength": 100, "minLength": 1, "pattern": "^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$"}` |
| `public_key` | Choose the OpenSSH public key file to associate with the remote account. | `required` | `{"format": "path"}` |
| `title` | Enter a recognizable display label for the selected record. | `required` | `{"maxLength": 80, "minLength": 1}` |

## Revoke owned SSH key

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `host` | Choose the hosting service for this account operation. | `"github.com"` | `{"maxLength": 253, "minLength": 1}` |
| `account` | Select the account whose session or resources this action will inspect. | `required` | `{"maxLength": 100, "minLength": 1, "pattern": "^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$"}` |
| `key_id` | Select the remote public key by its server-issued identifier. | `required` | `—` |

## Import repository identity

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `identity` | Name the local Git author identity used for this repository selection. | `required` | `{"maxLength": 64, "minLength": 1, "pattern": "^[a-z][a-z0-9_-]*$"}` |
| `repository` | Select the root of the Git checkout to inspect or configure. | `required` | `{"format": "path"}` |

## Apply identity to checkout

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `identity` | Name the local Git author identity used for this repository selection. | `required` | `{"maxLength": 64, "minLength": 1, "pattern": "^[a-z][a-z0-9_-]*$"}` |
| `repository` | Select the root of the Git checkout to inspect or configure. | `required` | `{"format": "path"}` |

## Revoke managed SSH keys

This operation has no editable parameters.

## Configure colors and aliases

This operation has no editable parameters.

## Verify SSH account access

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `host` | Choose the hosting service for this account operation. | `"github.com"` | `{"maxLength": 253, "minLength": 1}` |
| `account` | Select the account whose session or resources this action will inspect. | `required` | `{"maxLength": 100, "minLength": 1, "pattern": "^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$"}` |
| `public_key` | Choose the OpenSSH public key file to associate with the remote account. | `required` | `{"format": "path"}` |
| `login` | Enter the SSH account login shown by the hosting service. | `"git"` | `{"pattern": "^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$"}` |

## Remove managed local SSH keys

This operation has no editable parameters.

## Remove one formerly managed package

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `name` | Choose one package removed from this plugin's install list. | `required` | `{"maxLength": 128, "pattern": "^[a-z0-9][a-z0-9+.-]+$"}` |
