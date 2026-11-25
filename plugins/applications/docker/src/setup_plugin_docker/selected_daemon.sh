#!/bin/sh
# Send arguments to the reviewed local Engine after checking its public identity.
set -eu
check_global_options() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --host|--host=*|--context|--context=*) return 1 ;;
            --config|--log-level|--tlscacert|--tlscert|--tlskey)
                [ "$#" -gt 1 ] || return 0
                shift ;;
            --) return 0 ;;
            --*) ;;
            -?*)
                short=${1#-}
                while [ -n "$short" ]; do
                    case "$short" in
                        H*|c*) return 1 ;;
                        l)
                            [ "$#" -gt 1 ] || return 0
                            shift
                            break ;;
                        l*) break ;;
                    esac
                    short=${short#?}
                done ;;
            *) return 0 ;;
        esac
        shift
    done
}
if ! check_global_options "$@"; then
    echo 'Choose a different endpoint in the Docker mode action.' >&2
    exit 2
fi
unset DOCKER_CONTEXT DOCKER_HOST DOCKER_CONFIG DOCKER_TLS DOCKER_TLS_VERIFY DOCKER_CERT_PATH
config="${XDG_CONFIG_HOME:-$HOME/.config}/setup-system/docker/selected-daemon.json"
context=$(/usr/bin/jq -er '.context | strings | select(test("^[a-z][a-z0-9_-]{0,63}$"))' -- "$config")
host=$(/usr/bin/jq -er '.host | strings' -- "$config")
identity=$(/usr/bin/jq -er '.engine | strings' -- "$config")
case "$host" in
    unix:///run/docker.sock | unix:///var/run/docker.sock | "unix:///run/user/$(/usr/bin/id -u)/docker.sock") ;;
    *) echo 'Review the unsupported Docker endpoint.' >&2; exit 1 ;;
esac
selected=$(/usr/bin/docker context inspect "$context" --format '{{.Endpoints.docker.Host}}')
if [ "$selected" != "$host" ]; then
    echo 'Docker context changed.' >&2
    exit 1
fi
current=$(/usr/bin/docker --host "$host" info --format '{{.ID}}')
if [ "$current" != "$identity" ]; then
    echo 'Docker Engine changed.' >&2
    exit 1
fi
mode=$(/usr/bin/jq -er '.mode | select(. == "rootless" or . == "rootful")' -- "$config")
options=$(/usr/bin/docker --host "$host" info --format '{{json .SecurityOptions}}')
actual=$(printf '%s' "$options" | /usr/bin/jq -er '
    arrays | if index("name=rootless") == null then "rootful" else "rootless" end')
if [ "$actual" != "$mode" ]; then
    echo 'Docker daemon mode changed.' >&2
    exit 1
fi
exec /usr/bin/docker --host "$host" "$@"
