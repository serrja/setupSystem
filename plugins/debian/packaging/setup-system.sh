#!/bin/sh
if [ -z "${SETUP_SYSTEM_CATALOGS+x}" ]; then
    # JSON is passed to the host as one value, not evaluated as shell words.
    # shellcheck disable=SC2089
    SETUP_SYSTEM_CATALOGS='["/usr/lib/setup-system/plugin_catalog"]'
    # shellcheck disable=SC2090
    export SETUP_SYSTEM_CATALOGS
fi
SETUP_SYSTEM_UV=/usr/lib/setup-system/runtime/uv/$(dpkg --print-architecture)/uv
export SETUP_SYSTEM_UV
exec /var/lib/setup-system/active/host/bin/python -I -B -m setup_system "$@"
