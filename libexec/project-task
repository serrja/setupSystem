#!/bin/sh
set -eu
umask 077
if [ "$(pwd -P)" != /usr/lib/setup-system ] && [ -x .venv/bin/python ]; then
    exec .venv/bin/python -I -B -m setup_system.project_tasks "$@"
fi
if [ "$(pwd -P)" != /usr/lib/setup-system ] && [ -n "${SETUP_SYSTEM_INTERPRETER:-}" ]; then
    exec "$SETUP_SYSTEM_INTERPRETER" -I -B -m setup_system.project_tasks "$@"
fi
exec /usr/bin/python3 -I -B -m setup_system.project_tasks "$@"
