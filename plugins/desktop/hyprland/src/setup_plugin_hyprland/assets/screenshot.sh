#!/bin/sh
set -eu
area=$(slurp) || exit 0
target=$(mktemp "${XDG_RUNTIME_DIR:?}/setup-screenshot.XXXXXX.png")
trap 'rm -f "$target"' EXIT HUP INT TERM
grim -g "$area" "$target"
wl-copy --type image/png < "$target"
notify-send 'Screenshot' 'Selected area copied to clipboard'
