# shellcheck shell=sh
# Source from a runtime recipe before choosing its Python interpreter.
root=$(pwd -P)
installed_root=$(/usr/bin/python3 -I -B -c 'from setup_system.layout import INSTALLED; print(INSTALLED)' 2>/dev/null) || installed_root=
interpreter=/usr/bin/python3
if [ "$root" != "$installed_root" ]; then
    if [ -x "$root/.venv/bin/python" ]; then
        interpreter="$root/.venv/bin/python"
    elif [ -n "${SETUP_SYSTEM_INTERPRETER:-}" ]; then
        interpreter=$SETUP_SYSTEM_INTERPRETER
    fi
fi
# Used by the calling recipe, including its native playbook selection.
: "${interpreter:?}"
