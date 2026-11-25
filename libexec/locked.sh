#!/bin/sh
# Run one installed Make operation under the same writer lock as Debian delivery.
set -eu
lock=/run/setup-system
if [ ! -d "$lock" ] || [ -L "$lock" ] || [ "$(/usr/bin/stat -c '%u:%g:%a' -- "$lock")" != '0:0:755' ]; then
    exit 75
fi
exec /usr/bin/flock --exclusive --nonblock --conflict-exit-code 75 "$lock" \
    /usr/bin/python3 -I -B /usr/lib/setup-system/libexec/dpkg_lock.py "$@"
