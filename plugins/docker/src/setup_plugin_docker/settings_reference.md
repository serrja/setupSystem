# ⚙️ Docker settings reference

Generated from the plugin's Pydantic settings and action inputs.

## ⚙️ Settings

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `mode` | Rootful socket access grants root-equivalent control. Review the mode action to select an Engine. Saving this preference does not start a daemon or grant access. | `"rootless"` | `{"enum": ["rootless", "rootful"]}` |

## 🧩 Install

This operation has no editable parameters.

## 🧩 Update

This operation has no editable parameters.

## 🧩 Uninstall

This operation has no editable parameters.

## 🧩 Check Docker

This operation has no editable parameters.

## 🧩 Select local daemon context

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `context` | Use the rootless or default context when unselected. | `null` | `—` |

## 🧩 Create context

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `name` | Identify the Docker context whose configuration this action uses. | `required` | `{"maxLength": 64, "minLength": 1, "pattern": "^[a-z][a-z0-9_-]*$"}` |

## 🧩 Start service

This operation has no editable parameters.

## 🧩 Stop service

This operation has no editable parameters.

## 🧩 Registry login

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `host` | Choose the endpoint for this local Docker context. | `"docker.io"` | `{"maxLength": 253, "pattern": "^([a-z0-9]([a-z0-9-]*[a-z0-9])?\\.)+[a-z0-9-]+(:[0-9]{1,5})?$"}` |

## 🧩 Registry logout

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `host` | Choose the endpoint for this local Docker context. | `"docker.io"` | `{"maxLength": 253, "pattern": "^([a-z0-9]([a-z0-9-]*[a-z0-9])?\\.)+[a-z0-9-]+(:[0-9]{1,5})?$"}` |

## 🧩 Remove one unused image

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `digest` | Select an unused local image by its content digest. | `required` | `{"pattern": "^sha256:[a-f0-9]{64}$"}` |

## 🧩 Start system daemon

This operation has no editable parameters.

## 🧩 Stop system daemon

This operation has no editable parameters.

## 🧩 Configure rootless daemon logs

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `driver` | Choose the supported log driver for this Docker configuration. | `"local"` | `{"enum": ["local", "json-file"]}` |
| `log_size_mib` | Limit the size of an individual container log file in MiB. | `10` | `{"maximum": 1024, "minimum": 1}` |
| `log_files` | Set the maximum number of rotated log files kept per container. | `3` | `{"maximum": 64, "minimum": 1}` |

## 🧩 Remove owned client contexts

This operation has no editable parameters.
