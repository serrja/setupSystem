# Docker settings reference

Generated from the plugin's Pydantic settings and action inputs.

## Settings

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `mode` | Rootful socket access grants root-equivalent control. Group membership is never changed. | `"rootless"` | `{"enum": ["rootless", "rootful"]}` |

## Install

This operation has no editable parameters.

## Update

This operation has no editable parameters.

## Uninstall

This operation has no editable parameters.

## Check Docker

This operation has no editable parameters.

## Prepare daemon mode

This operation has no editable parameters.

## Create context

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `name` | New context name | `required` | `{"maxLength": 64, "minLength": 1, "pattern": "^[a-z][a-z0-9_-]*$"}` |

## Start service

This operation has no editable parameters.

## Stop service

This operation has no editable parameters.

## Registry login

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `host` | Host | `"docker.io"` | `{"maxLength": 253, "pattern": "^([a-z0-9]([a-z0-9-]*[a-z0-9])?\\.)+[a-z0-9-]+(:[0-9]{1,5})?$"}` |

## Registry logout

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `host` | Host | `"docker.io"` | `{"maxLength": 253, "pattern": "^([a-z0-9]([a-z0-9-]*[a-z0-9])?\\.)+[a-z0-9-]+(:[0-9]{1,5})?$"}` |

## Remove one unused image

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `digest` | Unused image digest | `required` | `{"pattern": "^sha256:[a-f0-9]{64}$"}` |

## Start system daemon

This operation has no editable parameters.

## Stop system daemon

This operation has no editable parameters.

## Configure rootless daemon logs

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `driver` | Container log driver | `"local"` | `{"enum": ["local", "json-file"]}` |
| `log_size_mib` | Maximum log file size | `10` | `{"maximum": 1024, "minimum": 1}` |
| `log_files` | Number of rotated log files | `3` | `{"maximum": 64, "minimum": 1}` |

## Remove owned client contexts

This operation has no editable parameters.
