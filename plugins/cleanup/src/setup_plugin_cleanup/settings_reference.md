# Cleanup settings reference

Generated from the plugin's Pydantic settings and action inputs.

## Settings

This operation has no editable parameters.

## Remove

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `packages` | Select each package included in the confirmed APT removal closure. | `required` | `{"maxItems": 64, "minItems": 1}` |

## Purge

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `packages` | Select each package included in the confirmed APT removal closure. | `required` | `{"maxItems": 64, "minItems": 1}` |

## Autoremove

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `packages` | Select each package included in the confirmed APT removal closure. | `required` | `{"maxItems": 64, "minItems": 1}` |
