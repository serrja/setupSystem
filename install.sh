#!/bin/sh
# Install or update a verified Debian package, or start the installed application.
set -eu
launcher_search_path=${PATH:-/usr/bin:/bin}
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
package_auth=sudo
usage() { printf '%s\n' 'Usage: sh install.sh install|update [--release vX.Y.Z] [--auth sudo|su]' '       sh install.sh run'; }
fail() { printf '%s\n' "$*" >&2; exit 1; }
check_platform() {
    [ "$(id -u)" != 0 ] || fail 'Run delivery as a normal user. Only the package transaction requires administrator authentication.'
    [ -r /etc/os-release ] || fail 'Cannot identify the Debian target.'
    # shellcheck disable=SC1091
    . /etc/os-release
    [ "${ID:-}:${VERSION_ID:-}:${VERSION_CODENAME:-}" = debian:13:trixie ] || fail 'A Debian 13 (trixie) target is required.'
    case "$(dpkg --print-architecture)" in amd64|arm64) ;; *) fail 'Supported architectures are amd64 and arm64.' ;; esac
}
run_package_transaction() {
    program=$1
    shift
    case "${package_auth:-sudo}" in
        sudo) sudo /bin/sh -ceu "$program" sh "$@" ;;
        su) su --shell /bin/sh --command "$program" -- root sh "$@" ;;
        *) fail 'Choose either sudo or su for package authentication.' ;;
    esac
}
package_transaction() {
# Root copies into its own staging directory and rechecks the exact authenticated bytes.
# These two literal scripts expand only inside their respective privileged shells.
# shellcheck disable=SC2016
run_package_transaction '
    set -eu
    PATH=/usr/sbin:/usr/bin:/sbin:/bin
    export PATH
    # Derive the file reader from the real, unprivileged ancestor via procfs.
    # su does not export the caller IDs and caller-supplied IDs are not authority.
    caller=$7
    case "$caller" in ""|*[!0-9]*) exit 1 ;; esac
    pid=$$
    while [ "$pid" != "$caller" ]; do
        [ "$pid" -gt 1 ] || exit 1
        pid=$(awk "/^PPid:/ {print \$2}" "/proc/$pid/status")
    done
    uid=$(awk "/^Uid:/ && \$2 == \$3 {print \$2}" "/proc/$caller/status")
    gid=$(awk "/^Gid:/ && \$2 == \$3 {print \$2}" "/proc/$caller/status")
    [ "${uid:-0}" -gt 0 ] && [ "${gid:-0}" -gt 0 ] || exit 1
    lock=/run/setup-system
    if [ ! -e "$lock" ]; then mkdir -m 0755 "$lock" || [ -d "$lock" ]; fi
    [ -d "$lock" ] && [ ! -L "$lock" ] && [ "$(stat -c "%u:%g:%a" "$lock")" = 0:0:755 ] || exit 1
    exec /usr/bin/env -i PATH="$PATH" HOME=/root LANG=C flock -xn -E 75 "$lock" /bin/sh -ceu "$1" sh "$2" "$3" "$4" "$5" "$6" "$uid" "$gid"
' '
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
    setpriv --reuid="$6" --regid="$7" --init-groups --no-new-privs \
        dd if="$src" iflag=nofollow,nonblock,count_bytes count=134217729 status=none > "$file"
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
' "$1" "$2" "$3" "$4" "$5" "$$" || {
    result=$?
    if [ "$result" = 75 ]; then
        echo 'setup-system is busy. Retry after the current operation finishes.' >&2
    elif [ "${package_auth:-sudo}" = su ]; then
        echo 'The authenticated package transaction did not complete. A locked root account requires an authorized administrator.' >&2
    fi
    exit "$result"
}
launch_command=/usr/bin/setup-system
if candidate=$(PATH=$launcher_search_path command -v setup-system); then
    if [ "$(readlink -f -- "$candidate")" = /usr/bin/setup-system ]; then
        launch_command=setup-system
    else
        echo 'Warning: another launcher earlier in your PATH shadows /usr/bin/setup-system.' >&2
    fi
fi
printf 'Start it separately with: %s\n' "$launch_command"
}
# The development Make target sources the same, restricted package transaction.
if [ "${0##*/}" = install-local.sh ]; then return 0; fi

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    run)
        [ "$#" -eq 1 ] || { usage >&2; exit 2; }
        [ "$(id -u)" != 0 ] || fail 'Start the application as the desktop user.'
        [ -x /usr/bin/setup-system ] || fail 'Install setup-system first. See https://github.com/serrja/setupSystem/blob/main/docs/debian.md for the install command.'
        exec /usr/bin/setup-system ;;
    install|update) action=$1; shift ;;
    *) usage >&2; exit 2 ;;
esac
tag=
auth_set=
while [ "$#" -gt 0 ]; do
    [ "$#" -ge 2 ] && [ -n "$2" ] || { usage >&2; exit 2; }
    case "$1" in
        --release) [ -z "$tag" ] || fail 'Choose one stable release.'; tag=$2 ;;
        --auth)
            [ -z "$auth_set" ] || fail 'Choose one authentication method.'
            case "$2" in sudo|su) package_auth=$2 ;; *) fail 'Choose sudo or su.' ;; esac
            auth_set=1 ;;
        *) usage >&2; exit 2 ;;
    esac
    shift 2
done
stable_tag() { printf '%s\n' "$1" | LC_ALL=C grep -Exq 'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)' && [ "$(printf '%s' "$1" | wc -l)" = 0 ]; }
[ -z "$tag" ] || stable_tag "$tag" || fail 'Expected a stable tag such as v1.2.3.'
check_platform
for tool in curl sha256sum "$package_auth" flock dpkg-deb dpkg-query; do
    command -v "$tool" >/dev/null || fail "Missing required delivery tool: $tool"
done
repository=serrja/setupSystem
web=https://github.com/$repository
api=https://api.github.com/repos/$repository
fetch() {
    url=$1
    timeout=60
    if [ "$3" -gt 4194304 ]; then timeout=300; fi
    for hop in 1 2 3; do
        reply=$(curl -q --proto '=https' --silent --connect-timeout 15 --max-time "$timeout" \
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
# The reviewed loader pins the complete executable inputs before they are used.
# A release API response or a checksum served beside an executable cannot change these pins.
arch=$(dpkg --print-architecture)
case "$arch" in
    amd64)
        gh_digest=1dac939af3e84fb3861927c984b357531341be9403b889e4aa86e8822145a09d
        jq_digest=b1c22172dd303f3be49e935aa56aa48a8b7a46e0bc838b4997d3bb451495870f ;;
    arm64)
        gh_digest=d17e9b089ce0356679c9fa1898f3a7d38dccf943d529acaa94c24e8a4584e0ae
        jq_digest=8b85c817833814ddca00a144c33705546355afccf0cf39b188f3cdb48b852309 ;;
    *) fail 'Unsupported verifier architecture.' ;;
esac
verified_download() {
    fetch "$1" "$2" "$3" || fail 'Could not download a pinned verification tool.'
    actual=$(sha256sum < "$2")
    [ "${actual%% *}" = "$4" ] || fail 'Verification tool checksum mismatch.'
}
verified_download "https://github.com/jqlang/jq/releases/download/jq-1.8.2/jq-linux-$arch" \
    "$dir/jq" 4194304 "$jq_digest"
chmod 0700 "$dir/jq"
mkdir -m 0700 "$dir/verifier-home"
jq() { /usr/bin/env -i PATH=/usr/bin:/bin HOME="$dir/verifier-home" "$dir/jq" "$@"; }
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
    | select((.assets | length) <= 512)
    | select([.assets[].name] as $names | ($names | unique | length) == ($names | length)
        and (([$n, ($n + ".sha256"), ($n + ".sigstore.json")] - $names) == [])
        and all($names[]; . == $n or . == ($n+".sha256") or . == ($n+".sigstore.json")
          or test("^[a-z][a-z0-9_-]{0,63}\\.json$")
          or test("^setup_system_plugin_[a-z0-9_]+-[0-9][a-zA-Z0-9.+_-]*\\.whl$")))
    | .assets[] | select(.name == $n) | .digest | select(test("^sha256:[a-f0-9]{64}$"))' "$dir/release") || fail 'Invalid Debian release inventory or asset digest.'
assets=$web/releases/download/$native
fetch "$assets/$file" "$dir/$file" 134217728
fetch "$assets/$file.sha256" "$dir/$file.sha256" 256
fetch "$assets/$file.sigstore.json" "$dir/$file.sigstore.json" 1048576
digest=$(sha256sum "$dir/$file" | cut -d ' ' -f 1)
printf '%s  %s\n' "$digest" "$file" > "$dir/expected"
[ "$published" = "sha256:$digest" ] || fail 'Debian asset digest mismatch.'
cmp -s "$dir/expected" "$dir/$file.sha256" || fail 'Debian package checksum mismatch.'
verified_download "https://github.com/cli/cli/releases/download/v2.105.0/gh_2.105.0_linux_$arch.deb" \
    "$dir/gh.deb" 33554432 "$gh_digest"
dpkg-deb -x "$dir/gh.deb" "$dir/verifier"
/usr/bin/env -i PATH=/usr/bin:/bin HOME="$dir/verifier-home" GH_HOST=github.com GH_PROMPT_DISABLED=1 \
    GH_NO_UPDATE_NOTIFIER=1 "$dir/verifier/usr/bin/gh" attestation verify "$dir/$file" --bundle "$dir/$file.sigstore.json" \
    --repo "$repository" --signer-workflow "$repository/.github/workflows/debian.yml" \
    --signer-digest "$sha" --source-ref refs/heads/main --source-digest "$sha" \
    --deny-self-hosted-runners --predicate-type https://slsa.dev/provenance/v1
# Additional download links inherit their identities from the attested package.
dpkg-deb -x "$dir/$file" "$dir/proven"
count=3
for member in "$dir"/proven/usr/lib/setup-system/plugin_catalog/*; do
    [ -e "$member" ] || continue
    [ -f "$member" ] && [ ! -L "$member" ] || fail 'Unexpected plugin catalog member.'
    name=${member##*/}
    evidence=$(sha256sum < "$member")
    jq -e --arg n "$name" --arg d "sha256:${evidence%% *}"         'any(.assets[]; .name == $n and .digest == $d)' "$dir/release" >/dev/null || fail 'Plugin release differs from the signed Debian catalog.'
    count=$((count+1))
done
jq -e --argjson count "$count" '.assets | length == $count' "$dir/release" >/dev/null || fail 'Release inventory differs from the signed Debian catalog.'
printf 'Verified %s from %s at %s.\n' "$file" "$repository" "$sha"
package_transaction "$dir/$file" "$digest" "$version" "$sha" "$action"
