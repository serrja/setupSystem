# Settings reference

Generated from the package resources and validation models. Run the reference check after changing a field. [Configuration](configuration.md) · [Development](development.md)

## Application environment

The normal-user launcher accepts the following environment variables. They override the numeric values in the packaged application.toml. Textual uses the suggestion settings and its Make runner uses the timeouts.

| Field | Type and limits | Default |
| --- | --- | --- |
| `SETUP_SYSTEM_SUGGESTION_DEBOUNCE_SECONDS` | number, min 0, max 1 | `0.075` |
| `SETUP_SYSTEM_SUGGESTION_CACHE_DIRECTORIES` | integer, min 1, max 128 | `32` |
| `SETUP_SYSTEM_SUGGESTION_SCAN_ENTRIES` | integer, min 1, max 8192 | `2048` |
| `SETUP_SYSTEM_SUGGESTION_RESULTS` | integer, min 1, max 512 | `128` |
| `SETUP_SYSTEM_PROMPT_TIMEOUT_SECONDS` | number, min 0.01, max 300 | `30.0` |
| `SETUP_SYSTEM_IDLE_TIMEOUT_SECONDS` | number, min 0.01, max 3600 | `90.0` |
| `SETUP_SYSTEM_TERMINATION_GRACE_SECONDS` | number, min 0.01, max 30 | `2.0` |
| `SETUP_SYSTEM_READ_ONLY_TIMEOUT_SECONDS` | number, min 0.01, max 86400 | `900.0` |

## Base system

The Base form, Preview and installed system play validate BaseOptions. The model defines the initial state. An explicitly selected packaged profile, saved base.toml and confirmed form values take increasing precedence.

| Field | Type and limits | Default |
| --- | --- | --- |
| `hyprland` | boolean | `false` |
| `graphical_login` | boolean | `false` |
| `plymouth` | boolean | `false` |
| `timezone` | string, max length 100, `^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$` or null | `unset` |
| `locale` | string, max length 64, `^(C\|[a-z]{2,3}_[A-Z]{2})\.UTF-8$` or null | `unset` |
| `keyboard` | string, `^[a-z][a-z0-9_-]{0,15}(,[a-z][a-z0-9_-]{0,15}){0,3}$` or null | `unset` |

## Working environment

The Workspace form and its plays validate WorkspaceOptions. The profile, saved workspace.toml and confirmed values follow the same precedence. Cleanup requires a separate preview.

| Field | Type and limits | Default |
| --- | --- | --- |
| `desktop_theme` | boolean | `false` |
| `theme` | Theme | `see nested fields` |
| `monitors` | list of MonitorLayout, max items 64 | `[]` |
| `apps` | list of string, `^[a-z][a-z0-9_-]{0,63}$`, max items 128 | `[]` |
| `languages` | list of string, `^[a-z][a-z0-9_-]{0,63}$`, max items 128 | `[]` |
| `cleanup` | list of string, `^[a-z][a-z0-9_-]{0,63}$`, max items 128 | `[]` |
| `cleanup_mode` | remove, purge, autoremove | `"remove"` |
| `project` | string, min length 1, max length 4000, `^[^\x00-\x1f\x7f]+$` or null | `unset` |

## Workspace theme

The theme table in workspace.toml supplies the desktop renderers and uses these model defaults for missing fields.

| Field | Type and limits | Default |
| --- | --- | --- |
| `compact_panel` | boolean | `false` |
| `effects` | boolean | `false` |
| `opacity` | number, min 0.75, max 1 | `0.96` |
| `rounding` | integer, min 0, max 24 | `12` |

## Monitor layout

Each entry in workspace.toml monitors identifies a connected output and its advertised mode. The compositor supplies the observation used to validate Preview and Apply.

| Field | Type and limits | Default |
| --- | --- | --- |
| `output` | string, `^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$` | `required` |
| `mode` | string, `^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}@[0-9]{1,4}(\.[0-9]{1,5})?$` | `required` |
| `scale` | number, min 0.25, max 8 | `1` |
| `x` | integer, min -65535, max 65535 | `0` |
| `y` | integer, min -65535, max 65535 | `0` |

## Packaged desktop style

The packaged shared/desktop.toml supplies Waybar and native renderers. The installed package owns these values.

| Field | Type and limits | Default |
| --- | --- | --- |
| `auth_screen` | AuthScreen | `see nested fields` |
| `fonts` | DesktopFonts | `see nested fields` |
| `panel_height` | integer, min 16, max 128 | `36` |
| `compact_panel_height` | integer, min 16, max 128 | `30` |

## Packaged desktop fonts

Foot, Mako, Waybar and fontconfig consume the fonts table.

| Field | Type and limits | Default |
| --- | --- | --- |
| `family` | string, `^[A-Za-z0-9 ]{1,48}$` | `"JetBrains Mono"` |
| `emoji_fallback` | string, `^[A-Za-z0-9 ]{1,48}$` | `"Noto Color Emoji"` |
| `ui_size` | integer, min 6, max 48 | `10` |
| `terminal_size` | integer, min 6, max 48 | `11` |
| `panel_size` | integer, min 6, max 48 | `12` |

## Packaged authentication style

The greetd greeter and hyprlock consume the auth_screen table with color roles from shared/palette.toml.

| Field | Type and limits | Default |
| --- | --- | --- |
| `font` | string, `^[A-Za-z0-9 ]{1,48}$` | `"Noto Sans"` |
| `font_size` | integer, min 10, max 48 | `16` |
| `input_width` | integer, min 384, max 640 | `384` |
| `input_height` | integer, min 32, max 96 | `48` |
| `spacing` | integer, min 8, max 48 | `16` |
| `radius` | integer, min 0, max 32 | `12` |
| `border` | integer, min 1, max 4 | `2` |
