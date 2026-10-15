#!/bin/sh
# Compatibility entry point. Interactive Zsh must not run an installer.
if [ -n "${ZSH_VERSION-}" ]; then
    return 0 2>/dev/null || exit 0
fi
if ! command -v python3 >/dev/null 2>&1; then
    printf 'Python 3.12+ is required. Install Ubuntu python3 first.\n' >&2
    exit 2
fi
exec python3 "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/bootstrap.py" "$@"
