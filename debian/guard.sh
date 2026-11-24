#!/bin/sh
# Refuse package changes while another process is applying workstation settings.
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
lock=/run/setup-system
if [ ! -e "$lock" ]; then
    mkdir -m 0755 "$lock" || [ -d "$lock" ]
fi
[ -d "$lock" ] && [ ! -L "$lock" ] && [ "$(stat -c '%u:%g:%a' "$lock")" = 0:0:755 ] || exit 1
if flock -xn "$lock" true; then exit 0; fi
# The verified delivery wrapper holds the exclusive lock across apt and dpkg.
# Only a lock held by an actual ancestor belongs to this package transaction.
owner=$(lslocks --raw --noheadings --notruncate --output PID,TYPE,MODE,PATH |
    awk -v path="$lock" '$2 == "FLOCK" && $3 == "WRITE" && $4 == path { print $1 }')
owner_uid=0
pid=$$
while [ "$pid" -gt 1 ]; do
    if [ "$pid" = "$owner" ]; then
        [ "$(awk '$1 == "Uid:" { print $3 }' "/proc/$pid/status")" != "$owner_uid" ] || exit 0
    fi
    pid=$(awk '$1 == "PPid:" { print $2 }' "/proc/$pid/status")
done
echo 'setup-system is busy. Retry after the current operation finishes.' >&2
exit 1
