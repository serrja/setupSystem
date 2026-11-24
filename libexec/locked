#!/bin/sh
# Installed tasks and package updates take an exclusive lock on the same directory.
set -eu
lock=/run/setup-system
if [ ! -d "$lock" ] && [ "$(pwd -P)" != /usr/lib/setup-system ]; then
    lock=.
fi
# Direct dpkg already owns its native POSIX lock when its maintainer guard runs.
# Check for that writer while holding our flock to close the post-preinst window.
code=0
/usr/bin/flock --exclusive --nonblock --conflict-exit-code 75 "$lock" /usr/bin/python3 -I -B "$(dirname -- "$0")/dpkg_lock.py" "$@" || code=$?
if [ "$code" = 75 ] && [ -n "${SETUP_SYSTEM_REQUEST:-}" ] && [ -n "${SETUP_SYSTEM_RESULT:-}" ]; then
    stage=$(dirname -- "$SETUP_SYSTEM_REQUEST")
    owner=$(id -u)
    if [ "$SETUP_SYSTEM_RESULT" = "$stage/result.json" ] &&
        [ "$(stat -c '%u:%a:%F' -- "$stage")" = "$owner:700:directory" ] &&
        [ "$(stat -c '%u:%a:%F' -- "$SETUP_SYSTEM_REQUEST")" = "$owner:600:regular file" ]; then
        report=$(mktemp "$stage/blocked.XXXXXXXX")
        printf '%s\n' '{"status":"blocked","blockers":["Another package or setup-system operation holds the writer lock. Retry after it finishes."]}' > "$report"
        if [ ! -e "$SETUP_SYSTEM_RESULT" ] && [ ! -L "$SETUP_SYSTEM_RESULT" ]; then
            if ! ln -T -- "$report" "$SETUP_SYSTEM_RESULT"; then
                echo "Could not publish the operation lock status." >&2
            fi
        fi
        rm -f -- "$report"
    fi
fi
exit "$code"
