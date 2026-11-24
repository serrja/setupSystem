#!/bin/sh
# Install or update a verified Debian package, or start the installed application.
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
usage() { printf '%s\n' 'Usage: sh install.sh install|update [--release vX.Y.Z]' '       sh install.sh run'; }
fail() { printf '%s\n' "$*" >&2; exit 1; }
check_platform() {
    [ "$(id -u)" != 0 ] || fail 'Run delivery as a normal user. Only the package transaction uses sudo.'
    [ -r /etc/os-release ] || fail 'Cannot identify the Debian target.'
    # shellcheck disable=SC1091
    . /etc/os-release
    [ "${ID:-}:${VERSION_ID:-}:${VERSION_CODENAME:-}" = debian:13:trixie ] || fail 'A Debian 13 (trixie) target is required.'
    case "$(dpkg --print-architecture)" in amd64|arm64) ;; *) fail 'Supported architectures are amd64 and arm64.' ;; esac
}
package_transaction() {
# Root copies into its own staging directory and rechecks the exact authenticated bytes.
sudo /bin/sh -ceu '
    PATH=/usr/sbin:/usr/bin:/sbin:/bin
    export PATH
    lock=/run/setup-system
    if [ ! -e "$lock" ]; then mkdir -m 0755 "$lock" || [ -d "$lock" ]; fi
    [ -d "$lock" ] && [ ! -L "$lock" ] && [ "$(stat -c "%u:%g:%a" "$lock")" = 0:0:755 ] || exit 1
    exec flock -xn -E 75 "$lock" /bin/sh -ceu "$1" sh "$2" "$3" "$4" "$5" "$6"
' sh '
    src=$1 digest=$2 version=$3 source=$4 action=$5
    current=$(dpkg-query -W -f="\${db:Status-Status} \${Version}" setup-system 2>/dev/null) || current=
    case "$current" in
        "installed "*)
            current=${current#installed }
            dpkg --compare-versions "$version" ge "$current" || { echo "Refusing a downgrade from $current." >&2; exit 1; }
            if [ "$version" = "$current" ]; then
                [ "$(dpkg-query -W -f="\${X-Setup-System-Source}" setup-system)" = "$source" ] || {
                    echo "Installed version has a different source identity." >&2; exit 1;
                }
                echo "setup-system $version is already installed."
                exit 0
            fi ;;
        ""|"config-files "*)
            [ "$action" = install ] || { echo "Install setup-system before updating." >&2; exit 1; } ;;
        *) echo "Resolve the incomplete dpkg transaction before retrying." >&2; exit 1 ;;
    esac
    stage=$(mktemp -d /run/setup-system/deb.XXXXXXXX)
    trap '\''rm -rf -- "$stage"'\'' EXIT
    trap '\''exit 130'\'' HUP INT TERM
    file=$stage/setup-system_${version}_all.deb
    setpriv --reuid="$SUDO_UID" --regid="$SUDO_GID" --init-groups --no-new-privs \
        head -c 134217729 -- "$src" > "$file"
    actual=$(sha256sum "$file")
    [ "${actual%% *}" = "$digest" ]
    [ "$(dpkg-deb -f "$file" Package)" = setup-system ]
    [ "$(dpkg-deb -f "$file" Version)" = "$version" ]
    [ "$(dpkg-deb -f "$file" Architecture)" = all ]
    [ "$(dpkg-deb -f "$file" X-Setup-System-Source)" = "$source" ]
    chmod 0644 "$file"
    chmod 0755 "$stage"
    apt-get --no-remove install "$file"
    [ "$(dpkg-query -W -f="\${db:Status-Status} \${Version}" setup-system)" = "installed $version" ]
' "$1" "$2" "$3" "$4" "$5" || {
    result=$?
    [ "$result" != 75 ] || echo 'setup-system is busy. Retry after the current operation finishes.' >&2
    exit "$result"
}
printf '%s\n' 'Start it separately with: setup-system'
}
# The development Make target sources the same, restricted package transaction.
if [ "${0##*/}" = install-local ]; then return 0; fi

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    run)
        [ "$#" -eq 1 ] || { usage >&2; exit 2; }
        [ "$(id -u)" != 0 ] || fail 'Start the application as the desktop user.'
        [ -x /usr/bin/setup-system ] || fail 'Install setup-system before using run.'
        exec /usr/bin/setup-system ;;
    install|update) action=$1; shift ;;
    *) usage >&2; exit 2 ;;
esac
tag=
if [ "$#" != 0 ]; then
    [ "$#" = 2 ] && [ "$1" = --release ] && [ -n "$2" ] || { usage >&2; exit 2; }
    tag=$2
fi
stable_tag() { printf '%s\n' "$1" | LC_ALL=C grep -Exq 'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)' && [ "$(printf '%s' "$1" | wc -l)" = 0 ]; }
[ -z "$tag" ] || stable_tag "$tag" || fail 'Expected a stable tag such as v1.2.3.'
check_platform
for tool in curl jq gh sha256sum sudo flock dpkg-deb dpkg-query; do
    command -v "$tool" >/dev/null || fail "Missing required delivery tool: $tool"
done
help=$(gh attestation verify --help) || fail 'Install the official GitHub CLI with attestation support.'
for flag in --bundle --signer-workflow --signer-digest --source-ref --source-digest --deny-self-hosted-runners --predicate-type; do
    printf '%s\n' "$help" | grep -Fq -- "$flag" || fail "GitHub CLI lacks $flag. Install the official GitHub CLI (tested with 2.98.0)."
done
repository=serrja/setupSystem
web=https://github.com/$repository
api=https://api.github.com/repos/$repository
fetch() {
    url=$1
    for hop in 1 2 3; do
        reply=$(curl -q --proto '=https' --silent --connect-timeout 15 --max-time 60 \
            --max-filesize "$3" --output "$2" --write-out '%{http_code} %{redirect_url}' "$url") || {
            echo 'Release HTTPS request failed or exceeded its limit.' >&2; return 1;
        }
        code=${reply%% *}
        case "$code" in
            200) [ "$(wc -c < "$2")" -le "$3" ] || return 1; return 0 ;;
            301|302|303|307|308)
                [ "$hop" != 3 ] || return 1
                case "$url" in "$api"/*) return 1 ;; esac
                url=${reply#* }
                case "$url" in
                    https://release-assets.githubusercontent.com/*|https://objects.githubusercontent.com/*) ;;
                    *) echo 'Unexpected release asset redirect.' >&2; return 1 ;;
                esac ;;
            *) echo "Release download returned HTTP $code." >&2; return 1 ;;
        esac
    done
}
if [ -z "$tag" ]; then
    location=$(curl -q --proto '=https' --silent --fail --head --output /dev/null \
        --connect-timeout 15 --max-time 30 --write-out '%{redirect_url}' "$web/releases/latest") || fail 'Cannot resolve the latest stable GitHub release.'
    case "$location" in "$web"/releases/tag/*) tag=${location#"$web/releases/tag/"} ;; *) fail 'Unexpected latest release location.' ;; esac
    stable_tag "$tag" || fail 'The latest release does not identify a stable version.'
fi
version=${tag#v}
native=deb-$tag
file=setup-system_${version}_all.deb
dir=$(mktemp -d)
trap 'rm -rf -- "$dir"' EXIT
trap 'exit 130' HUP INT TERM
commit() {
    fetch "$api/git/ref/tags/$1" "$dir/ref" 65536 || return 1
    jq -e --arg ref "refs/tags/$1" '.ref == $ref' "$dir/ref" >/dev/null || return 1
    kind=$(jq -er '.object.type' "$dir/ref") || return 1
    object=$(jq -er '.object.sha | select(test("^[0-9a-f]{40}$"))' "$dir/ref") || return 1
    case "$kind" in
        commit) printf '%s\n' "$object" ;;
        tag)
            fetch "$api/git/tags/$object" "$dir/tag" 65536 || return 1
            jq -er --arg tag "$1" 'select(.tag == $tag and .object.type == "commit") | .object.sha | select(test("^[0-9a-f]{40}$"))' "$dir/tag" ;;
        *) return 1 ;;
    esac
}
sha=$(commit "$tag") || fail 'Invalid canonical release tag.'
native_sha=$(commit "$native") || fail 'Verified Debian companion release is unavailable for this stable version.'
[ "$native_sha" = "$sha" ] || fail 'Native and canonical releases have different sources.'
fetch "$api/releases/tags/$tag" "$dir/canonical" 1048576
jq -e --arg tag "$tag" '.tag_name == $tag and .draft == false and .prerelease == false and (.published_at | type == "string" and length > 0)' "$dir/canonical" >/dev/null
fetch "$api/releases/tags/$native" "$dir/release" 1048576
published=$(jq -er --arg t "$native" --arg n "$file" '
    select(.tag_name == $t and .draft == false and .prerelease == false and (.published_at | type == "string" and length > 0))
    | select(([.assets[].name] | sort) == ([$n, ($n + ".sha256"), ($n + ".sigstore.json")] | sort))
    | .assets[] | select(.name == $n) | .digest | select(test("^sha256:[a-f0-9]{64}$"))' "$dir/release") || fail 'Invalid Debian release inventory or asset digest.'
assets=$web/releases/download/$native
fetch "$assets/$file" "$dir/$file" 134217728
fetch "$assets/$file.sha256" "$dir/$file.sha256" 256
fetch "$assets/$file.sigstore.json" "$dir/$file.sigstore.json" 1048576
digest=$(sha256sum "$dir/$file" | cut -d ' ' -f 1)
printf '%s  %s\n' "$digest" "$file" > "$dir/expected"
[ "$published" = "sha256:$digest" ] || fail 'Debian asset digest mismatch.'
cmp -s "$dir/expected" "$dir/$file.sha256" || fail 'Debian package checksum mismatch.'
mkdir -m 0700 "$dir/gh-home"
(
unset GH_TOKEN GITHUB_TOKEN GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN GH_CONFIG_DIR XDG_CONFIG_HOME XDG_CACHE_HOME XDG_DATA_HOME
HOME=$dir/gh-home GH_HOST=github.com gh attestation verify "$dir/$file" --bundle "$dir/$file.sigstore.json" \
    --repo "$repository" --signer-workflow "$repository/.github/workflows/debian.yml" \
    --signer-digest "$sha" --source-ref refs/heads/main --source-digest "$sha" \
    --deny-self-hosted-runners --predicate-type https://slsa.dev/provenance/v1
)
printf 'Verified %s from %s at %s.\n' "$file" "$repository" "$sha"
package_transaction "$dir/$file" "$digest" "$version" "$sha" "$action"
