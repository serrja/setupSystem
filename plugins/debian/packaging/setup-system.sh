#!/bin/sh
if [ -z "${SETUP_SYSTEM_CATALOGS+x}" ]; then
    SETUP_SYSTEM_CATALOGS='["/usr/lib/setup-system/plugin_catalog"]'
    export SETUP_SYSTEM_CATALOGS
fi
exec /usr/bin/python3 -I -B -m setup_system "$@"
