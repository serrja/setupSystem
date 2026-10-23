#!/bin/sh
# Runs only inside the selected Hyprland session, never from shell startup.
set -eu
: "${HYPRLAND_INSTANCE_SIGNATURE:?Hyprland session required}"
: "${XDG_RUNTIME_DIR:?Runtime directory required}"
profile_dir="${XDG_CONFIG_HOME:-$HOME/.config}/hypr/setup-system"
exec 9>"$XDG_RUNTIME_DIR/setup-hyprland-$HYPRLAND_INSTANCE_SIGNATURE.lock"
flock -n 9 || exit 0
export XDG_CURRENT_DESKTOP=Hyprland
export XDG_SESSION_TYPE=wayland
dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE HYPRLAND_INSTANCE_SIGNATURE
systemctl --user start pipewire.socket pipewire-pulse.socket wireplumber.service
children=""
cleanup() {
    for child in $children; do kill "$child" 2>/dev/null || true; done
    wait || true
}
trap cleanup EXIT HUP INT TERM
start() { "$@" & children="$children $!"; }
# Every process belongs to this helper. No global enable/restart or killall.
start waybar -c "$profile_dir/waybar.json" -s "$profile_dir/style.css"
start mako --config "$profile_dir/mako.conf"
start hypridle -c "$profile_dir/hypridle.conf"
start nm-applet --indicator
start /usr/libexec/hyprpolkitagent
start wl-paste --type text --watch cliphist store
start wl-paste --type image --watch cliphist store
while hyprctl -j monitors >/dev/null 2>&1; do sleep 3; done
