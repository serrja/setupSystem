#!/bin/sh
# Change only an available backlight through the active user's logind session.
set -eu

brightness_number() {
    case ${1-} in ''|*[!0-9]*|0[0-9]*) return 1 ;; esac
    [ "${#1}" -le 10 ] && [ "$1" -le 4294967295 ]
}

brightness_main() {
    brightness_mode=${1-}
    brightness_root=${2-/sys/class/backlight}
    case "$brightness_mode" in up|down) ;; *) printf '%s\n' 'Choose brightness up or down.' >&2; return 2 ;; esac
    [ "$(id -u)" -ne 0 ] || { printf '%s\n' 'Brightness control requires the desktop user.' >&2; return 4; }
    brightness_device=''
    for brightness_entry in "$brightness_root"/*; do
        [ -r "$brightness_entry/max_brightness" ] && [ -r "$brightness_entry/brightness" ] || continue
        brightness_name=${brightness_entry##*/}
        case "$brightness_name" in ''|.*|*[!A-Za-z0-9_.:-]*) continue ;; esac
        IFS= read -r brightness_max < "$brightness_entry/max_brightness" || continue
        brightness_number "$brightness_max" && [ "$brightness_max" -gt 0 ] || continue
        if [ -z "$brightness_device" ]; then brightness_device=$brightness_entry; fi
        if [ -r "$brightness_entry/type" ]; then
            IFS= read -r brightness_type < "$brightness_entry/type" || continue
            if [ "$brightness_type" = raw ]; then brightness_device=$brightness_entry; break; fi
        fi
    done
    [ -n "$brightness_device" ] || { printf '%s\n' 'No supported backlight device; monitor brightness may require its own controls.' >&2; return 4; }
    IFS= read -r brightness_max < "$brightness_device/max_brightness" || return 4
    IFS= read -r brightness_current < "$brightness_device/brightness" || return 4
    brightness_number "$brightness_current" || { printf '%s\n' 'Invalid backlight brightness value.' >&2; return 4; }
    brightness_session=/org/freedesktop/login1/session/auto
    brightness_interface=org.freedesktop.login1.Session
    brightness_active=$(busctl --system --timeout=5 get-property org.freedesktop.login1 "$brightness_session" "$brightness_interface" Active) || return 4
    brightness_remote=$(busctl --system --timeout=5 get-property org.freedesktop.login1 "$brightness_session" "$brightness_interface" Remote) || return 4
    [ "$brightness_active" = 'b true' ] && [ "$brightness_remote" = 'b false' ] || {
        printf '%s\n' 'Brightness control requires a local active logind session.' >&2; return 4;
    }
    brightness_step=$(((brightness_max * 5 + 99) / 100))
    brightness_minimum=$(((brightness_max * 2 + 99) / 100))
    if [ "$brightness_mode" = up ]; then
        brightness_target=$((brightness_current + brightness_step))
    else
        brightness_target=$((brightness_current - brightness_step))
    fi
    [ "$brightness_target" -ge "$brightness_minimum" ] || brightness_target=$brightness_minimum
    [ "$brightness_target" -le "$brightness_max" ] || brightness_target=$brightness_max
    busctl --system --timeout=5 call org.freedesktop.login1 "$brightness_session" "$brightness_interface" \
        SetBrightness ssu backlight "${brightness_device##*/}" "$brightness_target"
}

if [ "${0##*/}" = brightness.sh ]; then
    [ "$#" -eq 1 ] || { printf '%s\n' 'Usage: brightness.sh up|down' >&2; exit 2; }
    brightness_main "$1" /sys/class/backlight
fi
