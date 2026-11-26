# shellcheck shell=sh
# Select the interpreter for a normal-user application or migration recipe.
set -eu
[ "$(id -u)" != 0 ] || { echo 'Run setup-system as a normal user.' >&2; exit 2; }
case "$(pwd -P)" in
    /usr/lib/setup-system) interpreter=/usr/bin/python3 ;;
    *) if [ -x .venv/bin/python ]; then interpreter=.venv/bin/python; else interpreter=python3; fi ;;
esac
: "${interpreter:?}"
