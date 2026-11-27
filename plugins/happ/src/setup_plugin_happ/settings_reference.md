# ⚙️ Happ settings reference

Generated from the plugin's Pydantic settings and action inputs.

## ⚙️ Settings

This operation has no editable parameters.

## 🧩 Check Happ

This operation has no editable parameters.

## 🧩 Save subscription details

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `subscription` | Identify the local subscription whose settings or routes this action uses. | `required` | `{"maxLength": 48, "pattern": "^[a-z][a-z0-9_-]{0,47}$"}` |
| `title` | Enter a recognizable display label for the selected record. | `required` | `{"maxLength": 80, "minLength": 1}` |
| `source_type` | Select the subscription format provided by the VPN service. | `"standard"` | `{"enum": ["standard", "provider-json"]}` |
| `url_handling` | Choose the system keyring or enter the URL directly in Happ at each import. | `"system-keyring"` | `{"enum": ["system-keyring", "enter-in-happ"]}` |
| `url_reference` | Store the HTTPS URL in the system keyring. | `null` | `—` |

## 🧩 Import in Happ

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `subscription` | Identify the local subscription whose settings or routes this action uses. | `required` | `{"maxLength": 48, "pattern": "^[a-z][a-z0-9_-]{0,47}$"}` |

## 🧩 Refresh in Happ

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `subscription` | Identify the local subscription whose settings or routes this action uses. | `required` | `{"maxLength": 48, "pattern": "^[a-z][a-z0-9_-]{0,47}$"}` |

## 🧩 Remove in Happ

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `subscription` | Identify the local subscription whose settings or routes this action uses. | `required` | `{"maxLength": 48, "pattern": "^[a-z][a-z0-9_-]{0,47}$"}` |

## 🧩 Add or move a route rule

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `subscription` | Identify the local subscription whose settings or routes this action uses. | `required` | `{"maxLength": 48, "pattern": "^[a-z][a-z0-9_-]{0,47}$"}` |
| `route_name` | Name the routing configuration to export for the selected subscription. | `"user-route"` | `{"maxLength": 48, "pattern": "^[a-z][a-z0-9_-]{0,47}$"}` |
| `disposition` | Route matching traffic directly, through the proxy or to the block list. | `"direct"` | `{"enum": ["direct", "proxy", "block"]}` |
| `domain` | Match this exact DNS name when applying the routing rule. | `null` | `—` |
| `network` | Match addresses within this IP network when applying the routing rule. | `null` | `—` |
| `position` | Place this rule at the beginning or end of its routing list. | `"last"` | `{"enum": ["last", "first"]}` |
| `domestic_dns` | Leave blank to retain the route's current DNS settings. | `null` | `—` |
| `strategy` | Choose how Happ resolves domain names while matching this route. | `"IPIfNonMatch"` | `{"enum": ["AsIs", "IPIfNonMatch"]}` |

## 🧩 Reconnect in Happ

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `subscription` | Identify the local subscription whose settings or routes this action uses. | `required` | `{"maxLength": 48, "pattern": "^[a-z][a-z0-9_-]{0,47}$"}` |

## 🧩 Install

This operation has no editable parameters.

## 🧩 Update

This operation has no editable parameters.

## 🧩 Uninstall

This operation has no editable parameters.

## 🧩 Start Happ service

This operation has no editable parameters.

## 🧩 Stop Happ service

This operation has no editable parameters.

## 🧩 Forget local subscription records

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `subscription` | Identify the local subscription whose settings or routes this action uses. | `required` | `{"maxLength": 48, "pattern": "^[a-z][a-z0-9_-]{0,47}$"}` |

## 🧩 Forget managed subscriptions

This operation has no editable parameters.
