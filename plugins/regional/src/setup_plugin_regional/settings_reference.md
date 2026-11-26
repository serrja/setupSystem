# Regional settings settings reference

Generated from the plugin's Pydantic settings and action inputs.

## Settings

This operation has no editable parameters.

## Update regional tools

This operation has no editable parameters.

## Install regional tools

This operation has no editable parameters.

## Set timezone

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `value` | Use the selected native value for this operation. | `required` | `{"maxLength": 128, "pattern": "^[A-Za-z0-9/_.+-]+$"}` |

## Set locale

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `value` | Use the selected native value for this operation. | `required` | `{"maxLength": 32, "pattern": "^(C\|[a-z]{2,3}_[A-Z]{2})\\.UTF-8$"}` |

## Set keyboard

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `layouts` | The first selected layout is the default. | `required` | `{"maxItems": 4, "minItems": 1}` |
