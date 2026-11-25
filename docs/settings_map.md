# ⚙️ Settings reference

The launcher reads packaged settings and explicit environment overrides.
Each plugin ships a generated `settings_reference.md` beside its manifest and Help.

| Field | Type and limits | Default |
| --- | --- | --- |
| `SETUP_SYSTEM_SUGGESTION_DEBOUNCE_SECONDS` | number, min 0, max 1 | `0.075` |
| `SETUP_SYSTEM_SUGGESTION_CACHE_DIRECTORIES` | integer, min 1, max 128 | `32` |
| `SETUP_SYSTEM_SUGGESTION_SCAN_ENTRIES` | integer, min 1, max 8192 | `2048` |
| `SETUP_SYSTEM_SUGGESTION_RESULTS` | integer, min 1, max 512 | `128` |
| `SETUP_SYSTEM_PROMPT_TIMEOUT_SECONDS` | number, min 0.01, max 300 | `30.0` |
| `SETUP_SYSTEM_IDLE_TIMEOUT_SECONDS` | number, min 0.01, max 3600 | `90.0` |
| `SETUP_SYSTEM_TERMINAL_TIMEOUT_SECONDS` | number, min 1, max 3600 | `600.0` |
| `SETUP_SYSTEM_TERMINATION_GRACE_SECONDS` | number, min 0.01, max 30 | `2.0` |
| `SETUP_SYSTEM_READ_ONLY_TIMEOUT_SECONDS` | number, min 0.01, max 86400 | `900.0` |

See [Configuration](configuration.md) for profile storage and precedence.
