#!/bin/sh
set -eu
[ "$(id -u)" != 0 ] || { echo 'Run setup-system as a normal user.' >&2; exit 2; }
if [ "$(pwd -P)" = /usr/lib/setup-system ]; then
    exec /usr/bin/python3 -I -B -m setup_system.app
elif [ -x .venv/bin/python ]; then
    exec .venv/bin/python -I -B -m setup_system.app
else
    exec python3 -I -B -m setup_system.app
fi
