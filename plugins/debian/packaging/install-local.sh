#!/bin/sh
# Install the single package produced by an explicit local build.
set -eu
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck disable=SC1091
. "$script_dir/install.sh"
check_platform
[ "$#" = 1 ] || exit 2
case "$1" in /*) destination=$1 ;; *) destination="$PWD/$1" ;; esac
set -- "$destination"/*.deb
[ "$#" = 1 ] && [ -f "$1" ] && [ ! -L "$1" ] || fail invalid_input 'Expected exactly one regular .deb in the package output directory.'
file=$1
version=$(dpkg-deb -f "$file" Version)
sha=$(git -C "$script_dir" rev-parse HEAD)
[ "$(dpkg-deb -f "$file" X-Setup-System-Source)" = "$sha" ] || fail integrity_mismatch 'Package source differs from this checkout.'
[ "$(dpkg-deb -f "$file" Package)" = setup-system ] && [ "$(dpkg-deb -f "$file" Architecture)" = all ]
[ "${file##*/}" = "setup-system_${version}_all.deb" ]
actual=$(sha256sum "$file")
package_transaction "$file" "${actual%% *}" "$version" "$sha" install
