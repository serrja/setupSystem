# GitHub CLI settings reference

Generated from the plugin's Pydantic settings and action inputs.

## Settings

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `enterprise_host` | Enter the exact GitHub Enterprise DNS hostname you intend to use. | `""` | `{"maxLength": 253, "pattern": "^$\|^([a-z0-9]([a-z0-9-]*[a-z0-9])?\\.)+[a-z]([a-z0-9-]*[a-z0-9])?$"}` |

## Check Git

This operation has no editable parameters.

## Account status

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `host` | Hosting service | `"github.com"` | `{"maxLength": 253}` |
| `account` | Local hosting account | `required` | `{"maxLength": 100, "minLength": 1, "pattern": "^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$"}` |

## Sign in

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `host` | Host | `"github.com"` | `{"maxLength": 253, "minLength": 1}` |
| `account` | Account | `required` | `{"maxLength": 100, "minLength": 1, "pattern": "^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$"}` |

## Sign out

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `host` | Hosting service | `"github.com"` | `{"maxLength": 253}` |
| `account` | Local hosting account | `required` | `{"maxLength": 100, "minLength": 1, "pattern": "^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$"}` |

## Install

This operation has no editable parameters.

## Update

This operation has no editable parameters.

## Uninstall

This operation has no editable parameters.
