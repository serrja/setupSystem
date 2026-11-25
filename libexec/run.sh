#!/bin/sh
set -eu
[ "$(id -u)" != 0 ] || { echo 'Run setup-system as a normal user.' >&2; exit 2; }
# shellcheck source=libexec/runtime.sh
. "$(dirname -- "$0")/runtime.sh"
if [ "$root" != "$installed_root" ] && [ "$interpreter" = /usr/bin/python3 ]; then
    exec python3 -I -B -m setup_system.app
fi
exec "$interpreter" -I -B -m setup_system.app
