# Hyprland settings reference

Generated from the plugin's Pydantic settings and action inputs.

## Settings

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `effects` | Compositor effects | `false` | `—` |
| `rounding` | Window corner radius | `12` | `{"maximum": 24, "minimum": 0}` |
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
| `output` | Output | `required` | `{"pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$"}` |
| `mode` | Mode | `required` | `{"pattern": "^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}@[0-9]{1,4}(\\.[0-9]{1,5})?$"}` |
| `x` | X | `0` | `{"maximum": 65535, "minimum": -65535}` |
| `y` | Y | `0` | `{"maximum": 65535, "minimum": -65535}` |
| `scale` | Scale | `1` | `{"maximum": 8, "minimum": 0.25}` |

## Test monitor temporarily

| Field | Description | Default | Constraints |
| --- | --- | --- | --- |
| `output` | Output | `required` | `{"pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$"}` |
| `mode` | Mode | `required` | `{"pattern": "^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}@[0-9]{1,4}(\\.[0-9]{1,5})?$"}` |
| `x` | X | `0` | `{"maximum": 65535, "minimum": -65535}` |
| `y` | Y | `0` | `{"maximum": 65535, "minimum": -65535}` |
| `scale` | Scale | `1` | `{"maximum": 8, "minimum": 0.25}` |
