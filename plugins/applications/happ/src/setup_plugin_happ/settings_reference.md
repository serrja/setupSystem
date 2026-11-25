# Happ settings reference

Generated from the plugin's Pydantic settings and action inputs.

## Settings

This operation has no editable parameters.

## Check Happ

This operation has no editable parameters.

## Save subscription details

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `subscription` | Local subscription ID | `required` | `{"maxLength": 48, "pattern": "^[a-z][a-z0-9_-]{0,47}$"}` |
| `title` | Display name | `required` | `{"maxLength": 80, "minLength": 1}` |
| `source_type` | Source format | `"standard"` | `{"enum": ["standard", "provider-json"]}` |
| `url_handling` | Choose the system keyring or enter the URL directly in Happ at each import. | `"system-keyring"` | `{"enum": ["system-keyring", "enter-in-happ"]}` |
| `url_reference` | Store the HTTPS URL in the system keyring. | `null` | `—` |

## Import in Happ

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `subscription` | Subscription ID | `required` | `{"maxLength": 48, "pattern": "^[a-z][a-z0-9_-]{0,47}$"}` |

## Refresh in Happ

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `subscription` | Subscription ID | `required` | `{"maxLength": 48, "pattern": "^[a-z][a-z0-9_-]{0,47}$"}` |

## Remove in Happ

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `subscription` | Subscription ID | `required` | `{"maxLength": 48, "pattern": "^[a-z][a-z0-9_-]{0,47}$"}` |

## Add or move a route rule

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `subscription` | Subscription ID | `required` | `{"maxLength": 48, "pattern": "^[a-z][a-z0-9_-]{0,47}$"}` |
| `profile` | Route profile name | `"user-route"` | `{"maxLength": 48, "pattern": "^[a-z][a-z0-9_-]{0,47}$"}` |
| `disposition` | Rule action | `"direct"` | `{"enum": ["direct", "proxy", "block"]}` |
| `domain` | Exact DNS name | `null` | `—` |
| `network` | IPv4 or IPv6 network | `null` | `—` |
| `position` | Position in this rule list | `"last"` | `{"enum": ["last", "first"]}` |
| `domestic_dns` | Leave blank to retain the profile's current DNS settings. | `null` | `—` |
| `strategy` | Domain strategy | `"IPIfNonMatch"` | `{"enum": ["AsIs", "IPIfNonMatch"]}` |

## Reconnect in Happ

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `subscription` | Subscription ID | `required` | `{"maxLength": 48, "pattern": "^[a-z][a-z0-9_-]{0,47}$"}` |

## Install

This operation has no editable parameters.

## Update

This operation has no editable parameters.

## Uninstall

This operation has no editable parameters.

## Start Happ service

This operation has no editable parameters.

## Stop Happ service

This operation has no editable parameters.

## Forget local subscription records

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `subscription` | Subscription ID | `required` | `{"maxLength": 48, "pattern": "^[a-z][a-z0-9_-]{0,47}$"}` |

## Forget managed subscriptions

This operation has no editable parameters.
