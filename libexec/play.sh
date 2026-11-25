#!/bin/sh
# Execute one packaged play as the invoking user. Only individual tasks may become root.
set -eu
PATH=/usr/bin:/bin
export PATH
umask 077
# shellcheck source=libexec/runtime.sh
. "$(dirname -- "$0")/runtime.sh"
[ "$(id -u)" != 0 ] || { echo 'Run the configurator as a normal user.' >&2; exit 2; }
case "$1/$2" in
    admin/apply|base/apply|workspace/apply|workspace/cleanup-apply)
        [ "$root" = "$installed_root" ] || { echo 'Install the Debian package before system Apply.' >&2; exit 2; }
        /usr/bin/python3 -I -B -c 'from pathlib import Path; from setup_system.layout import verify_installation; verify_installation(Path.cwd())'
        set -- "$1" "$2" --ask-become-pass
        if [ "$1" = admin ]; then set -- "$@" --become-method su; fi
        ;;
    admin/inspect|admin/plan|base/inspect|base/plan|base/user-apply|workspace/inspect|workspace/plan|workspace/cleanup-plan|workspace/user-apply)
        set -- "$1" "$2"
        ;;
    *) echo 'Unknown packaged operation.' >&2; exit 2 ;;
esac
section=$1
action=$2
shift 2
request=${SETUP_SYSTEM_REQUEST:?Missing run snapshot}
result=${SETUP_SYSTEM_RESULT:?Missing run result destination}
stage=$(dirname -- "$request")
[ "$result" = "$stage/result.json" ] || exit 2
[ "$(stat -c '%u:%a:%F' -- "$stage")" = "$(id -u):700:directory" ] || exit 2
# Administrative Apply publishes its own result after a descriptor-based preflight.
if [ "$section/$action" != admin/apply ]; then
    [ "$(stat -c '%u:%a:%F' -- "$request")" = "$(id -u):600:regular file" ] || exit 2
fi
playbook=/usr/bin/ansible-playbook
if [ "$root" != "$installed_root" ] && [ "$interpreter" != /usr/bin/python3 ]; then
    playbook=$(dirname -- "$interpreter")/ansible-playbook
fi
[ -f "$playbook" ] || { echo 'Ansible is missing. Install the package or prepare the development dependencies.' >&2; exit 2; }
if [ "$section/$action" = admin/apply ]; then
    # Validate before Ansible can ask for credentials. The play rechecks this digest
    # after the writer lock and retains one snapshot for the privileged task.
    consent=$("$interpreter" -I -B "$root/ansible/files/admin.py" preflight "$request") || exit $?
    set -- "$@" -e "admin_request_sha=$consent"
fi
case "$action" in
    apply|user-apply|cleanup-apply) set -- /bin/sh "$root/libexec/locked.sh" "$interpreter" -I -B "$playbook" -i localhost, -c local "$root/ansible/$section.yml" -e "setup_action=$action" "$@" ;;
    *) set -- "$interpreter" -I -B "$playbook" -i localhost, -c local "$root/ansible/$section.yml" -e "setup_action=$action" "$@" ;;
esac
exec /usr/bin/env -i PATH=/usr/bin:/bin HOME="$HOME" LANG=C.UTF-8 \
    XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}" XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}" \
    XDG_STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}" XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}" \
    CODEX_HOME="${CODEX_HOME:-$HOME/.codex}" \
    XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" \
    HYPRLAND_INSTANCE_SIGNATURE="${HYPRLAND_INSTANCE_SIGNATURE:-}" WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-}" \
    SETUP_SYSTEM_REQUEST="$request" SETUP_SYSTEM_RESULT="$result" SETUP_SYSTEM_CANCEL="$stage/cancel" \
    ANSIBLE_CONFIG="$root/ansible.cfg" ANSIBLE_HOME=/usr/share/ansible \
    ANSIBLE_LOCAL_TEMP="$stage/local" ANSIBLE_REMOTE_TEMP="$stage/remote" \
    ANSIBLE_PIPELINING=true ANSIBLE_NOCOLOR=true ANSIBLE_BECOME_ASK_PASS=false \
    "$@"
