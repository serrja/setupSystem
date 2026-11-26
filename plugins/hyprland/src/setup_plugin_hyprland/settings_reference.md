# Hyprland settings reference

Generated from the plugin's Pydantic settings and action inputs.

## Settings

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `effects` | Enable the compositor effects when writing the Hyprland configuration. | `false` | `—` |
| `rounding` | Set the corner radius in pixels. | `12` | `{"maximum": 24, "minimum": 0}` |
| `keyboard` | Select up to four XKB layouts. The first is the session default. | `["us"]` | `{"maxItems": 4, "minItems": 1}` |

## Check program

This operation has no editable parameters.

## Install

This operation has no editable parameters.

## Update

This operation has no editable parameters.

## Uninstall

This operation has no editable parameters.

## Save configuration

This operation has no editable parameters.

## Save monitor layout

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `output` | Select a display connector reported by the current compositor session. | `required` | `{"pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$"}` |
| `mode` | Choose an advertised display mode for the selected connector. | `required` | `{"pattern": "^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}@[0-9]{1,4}(\\.[0-9]{1,5})?$"}` |
| `x` | Set the horizontal position of this output in the desktop layout. | `0` | `{"maximum": 65535, "minimum": -65535}` |
| `y` | Set the vertical position of this output in the desktop layout. | `0` | `{"maximum": 65535, "minimum": -65535}` |
| `scale` | Set the logical display scale for this output. | `1.0` | `{"maximum": 8.0, "minimum": 0.25}` |

## Test monitor temporarily

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `output` | Select a display connector reported by the current compositor session. | `required` | `{"pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$"}` |
| `mode` | Choose an advertised display mode for the selected connector. | `required` | `{"pattern": "^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}@[0-9]{1,4}(\\.[0-9]{1,5})?$"}` |
| `x` | Set the horizontal position of this output in the desktop layout. | `0` | `{"maximum": 65535, "minimum": -65535}` |
| `y` | Set the vertical position of this output in the desktop layout. | `0` | `{"maximum": 65535, "minimum": -65535}` |
| `scale` | Set the logical display scale for this output. | `1.0` | `{"maximum": 8.0, "minimum": 0.25}` |
