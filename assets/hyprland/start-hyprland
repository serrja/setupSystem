#!/bin/sh
# Managed setupSystem login entrypoint; run explicitly from a local TTY.
set -eu
[ "$(id -u)" -ne 0 ] || { printf '%s\n' 'Run Hyprland as the desktop user, never root.' >&2; exit 4; }
[ -z "${SSH_CONNECTION-}${SSH_TTY-}${WAYLAND_DISPLAY-}${DISPLAY-}" ] || {
    printf '%s\n' 'Start Hyprland from a local authenticated session, outside an existing graphical session.' >&2
    exit 4
}
case "${1-}" in
    '')
        console=$(tty) || exit 4
        case "$console" in /dev/tty[0-9]*) ;; *) printf '%s\n' 'A local Linux virtual console is required.' >&2; exit 4 ;; esac
        ;;
    --display-manager)
        [ "$#" -eq 1 ] && [ -n "${XDG_SESSION_ID-}" ] || exit 4
        timeout 5s /bin/sh -s -- "$XDG_SESSION_ID" "$(id -u)" <<'CHECK_SESSION' || exit 4
set -eu
while :; do
    details=$(timeout 1s loginctl --no-pager show-session "$1" --property=User --property=Remote --property=Class --property=Active --property=Seat --property=VTNr) || exit 4
    printf '%s\n' "$details" | grep -Fx "User=$2" >/dev/null || exit 4
    printf '%s\n' "$details" | grep -Fx 'Remote=no' >/dev/null || exit 4
    printf '%s\n' "$details" | grep -Fx 'Class=user' >/dev/null || exit 4
    printf '%s\n' "$details" | grep -E '^Seat=seat[[:alnum:]_-]+$' >/dev/null || exit 4
    printf '%s\n' "$details" | grep -E '^VTNr=[1-9][0-9]*$' >/dev/null || exit 4
    printf '%s\n' "$details" | grep -Fx 'Active=yes' >/dev/null && exit 0
    printf '%s\n' "$details" | grep -Fx 'Active=no' >/dev/null || exit 4
    sleep 0.2
done
CHECK_SESSION
        ;;
    *) printf '%s\n' 'Unknown session launcher argument.' >&2; exit 4 ;;
esac
: "${XDG_RUNTIME_DIR:?Log in locally so pam_systemd creates the user runtime directory}"
[ "$(stat -c %u -- "$XDG_RUNTIME_DIR")" = "$(id -u)" ] || exit 4
export XDG_CONFIG_HOME='@{xdg.config}@'
export XDG_DATA_HOME='@{xdg.data}@'
export XDG_STATE_HOME='@{xdg.state}@'
export XDG_CACHE_HOME='@{xdg.cache}@'
export PATH="$HOME/.local/bin:$PATH"
if command -v nvim >/dev/null 2>&1; then
    export EDITOR=nvim VISUAL=nvim GIT_EDITOR=nvim
fi
export XDG_CURRENT_DESKTOP=Hyprland
export XDG_SESSION_TYPE=wayland
if [ -n "${DBUS_SESSION_BUS_ADDRESS-}" ]; then
    exec /usr/bin/start-hyprland
fi
exec dbus-run-session -- /usr/bin/start-hyprland
