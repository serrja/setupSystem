#!/bin/sh
set -eu
umask 077
# shellcheck source=libexec/runtime.sh
. "$(dirname -- "$0")/runtime.sh"
case "$1" in
    files) module=setup_system.file_tasks ;;
    projects) module=setup_system.project_tasks ;;
    *) exit 2 ;;
esac
shift
exec "$interpreter" -I -B -m "$module" "$@"
