#!/bin/sh
# Optional desktop helpers fail independently; only this session owns their children.
set -eu
: "${HYPRLAND_INSTANCE_SIGNATURE:?Hyprland session required}"
: "${XDG_RUNTIME_DIR:?Runtime directory required}"
[ "$(id -u)" -ne 0 ] || { printf '%s\n' 'Desktop helpers must run as the ordinary user.' >&2; exit 4; }
profile_dir="${XDG_CONFIG_HOME:-$HOME/.config}/hypr/setup-system"
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/setup-system"
umask 077
mkdir -p -- "$state_dir"
log="$state_dir/hyprland-session.log"
if [ -f "$log" ] && [ "$(stat -c %s -- "$log")" -gt 1048576 ]; then
    mv -- "$log" "$log.previous"
fi
exec 8>>"$log"
exec 9>"$XDG_RUNTIME_DIR/setup-hyprland-$HYPRLAND_INSTANCE_SIGNATURE.lock"
flock -n 9 || exit 0
warn() { printf '%s\n' "setupSystem session: $*" >&8; }
export XDG_CURRENT_DESKTOP=Hyprland
export XDG_SESSION_TYPE=wayland
if ! dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE HYPRLAND_INSTANCE_SIGNATURE >&8 2>&8; then
    warn 'Could not import the current environment into the user manager; inspect the user bus.'
fi
if ! systemctl --user start pipewire.socket pipewire-pulse.socket wireplumber.service >&8 2>&8; then
    warn 'Audio user units did not start; the rest of the desktop remains available.'
fi
children=""
cleanup() {
    for child in $children; do
        if kill -0 "$child" 2>/dev/null; then kill "$child" 2>/dev/null || :; fi
    done
    wait || :
}
trap cleanup EXIT
trap 'exit 0' HUP INT TERM
start() {
    role=$1; shift
    if command -v "$1" >/dev/null 2>&1; then
        "$@" >&8 2>&8 &
        children="$children $!"
    else
        warn "$role is unavailable: $1"
    fi
}
start panel /usr/bin/python3 -I -B "$profile_dir/panel.py" "$profile_dir"
start notifications mako --config "$profile_dir/mako.conf"
start idle hypridle -c "$profile_dir/hypridle.conf"
start wallpaper swaybg -i "$profile_dir/wallpaper.svg" -m fill
if systemctl is-active --quiet NetworkManager.service; then start network nm-applet --indicator; fi
for controller in /sys/class/bluetooth/hci*; do
    [ -e "$controller" ] || continue
    start bluetooth blueman-applet
    break
done
start authentication /usr/libexec/hyprpolkitagent
start clipboard-text wl-paste --type text --watch cliphist store
start clipboard-image wl-paste --type image --watch cliphist store
while hyprctl -j monitors >/dev/null 2>&1; do sleep 3; done
