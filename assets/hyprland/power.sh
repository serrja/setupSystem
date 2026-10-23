#!/bin/sh
set -eu
choice=$(printf '%s\n' Lock Suspend Logout Reboot Poweroff | wofi --dmenu --prompt Power) || exit 0
case "$choice" in
    Lock) loginctl lock-session ;;
    Suspend) systemctl suspend ;;
    Logout|Reboot|Poweroff)
        confirm=$(printf '%s\n' Cancel Confirm | wofi --dmenu --prompt "$choice?") || exit 0
        [ "$confirm" = Confirm ] || exit 0
        case "$choice" in
            Logout) hyprctl dispatch 'hl.dsp.exit()' ;;
            Reboot) systemctl reboot ;;
            Poweroff) systemctl poweroff ;;
        esac ;;
esac
