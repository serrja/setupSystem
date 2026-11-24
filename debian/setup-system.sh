#!/bin/sh
set -eu
case "$#:${1:-}" in
    1:--version) exec /usr/bin/dpkg-query -W '-f=${Version}\n' setup-system ;;
    1:--help) printf '%s\n' 'Usage: setup-system [--help | --version]'; exit 0 ;;
    0:) ;;
    *) echo 'Usage: setup-system [--help | --version]' >&2; exit 2 ;;
esac
[ "$(/usr/bin/id -u)" != 0 ] || { echo 'Run setup-system as the desktop user.' >&2; exit 1; }
unset MAKEFLAGS GNUMAKEFLAGS MAKEFILES MAKEOVERRIDES MFLAGS
PATH=/usr/bin:/bin
export PATH
exec /usr/bin/make -rR -j1 --no-print-directory -C /usr/lib/setup-system -f /usr/lib/setup-system/Makefile run
