#!/usr/bin/env bash
# Canonical clean-Debian entrypoint. Never source this from shell startup.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

setup_error() { printf 'setupSystem: %s\n' "$*" >&2; return 2; }
setup_usage() {
    cat <<'HELP'
Usage: sudo bash ./setup.sh [apply] [--yes] [--user NAME]
       bash ./setup.sh {plan|--dry-run|--help}
       [--xdg-config-home PATH] [--xdg-data-home PATH]
       [--xdg-state-home PATH] [--xdg-cache-home PATH]
No arguments install Hyprland for the verified sudo caller. Direct root needs --user.
XDG paths must be inside that user's HOME; explicit options work without sudo -E.
Help/plan never install prerequisites, download files or refresh APT metadata.
Without Python, plan shows preliminary shell preflight. Apply bootstraps Python.
One confirmation authorizes signed Debian packages, managed backports and user files.
HELP
}
setup_parse() {
    mode=apply; consent=0; requested_user=''; xdg_arguments=(); forwarded=(); local verb='' want_help=0
    local -A seen=()
    while (($#)); do
        case "$1" in
            -h|--help) want_help=1; shift ;;
            apply|plan|--dry-run)
                [[ -z $verb ]] || { setup_error 'Choose only one apply/plan mode.'; return 2; }
                verb=$1; [[ $1 == apply ]] && mode=apply || mode=plan; shift ;;
            --yes) consent=1; shift ;;
            --user|--xdg-config-home|--xdg-data-home|--xdg-state-home|--xdg-cache-home)
                (($# >= 2)) && [[ -n $2 && $2 != --* ]] || { setup_error "Missing value for $1."; return 2; }
                if [[ $1 == --user ]]; then
                    [[ -z $requested_user ]] || { setup_error 'Duplicate --user.'; return 2; }
                    requested_user=$2
                else
                    [[ -z ${seen[$1]-} ]] || { setup_error "Duplicate $1."; return 2; }
                    seen[$1]=1; xdg_arguments+=("$1" "$2"); fi
                shift 2 ;;
            *) setup_error "Unknown or retired argument: $1. Use --help."; return 2 ;;
        esac
    done
    [[ $want_help == 0 ]] || mode=help
    [[ $mode != plan || $consent == 0 ]] || { setup_error '--yes belongs to apply.'; return 2; }
}
setup_release_value() {
    # Data only: never source os-release or a user configuration as root shell code.
    local value
    value=$(awk -F= -v key="$1" '$1==key {print substr($0,index($0,"=")+1);exit}' /etc/os-release)
    value=${value#\"}; value=${value%\"}; value=${value#\'}; value=${value%\'}
    printf '%s' "$value"
}
setup_manifest_value() {
    awk -F= -v key="$1" '
        /^\[hyprland\]$/ {selected=1;next} /^\[/ {selected=0}
        selected {name=$1;gsub(/[[:space:]]/,"",name);if(name==key){
            value=substr($0,index($0,"=")+1);gsub(/^[[:space:]]+|[[:space:]]+$/,"",value);
            gsub(/^"|"$/,"",value);print value;exit}}' "$setup_root/manifests/platforms.toml"
}
setup_platform() {
    local distro version codename arch allowed
    distro=$(setup_release_value ID); version=$(setup_release_value VERSION_ID)
    codename=$(setup_release_value VERSION_CODENAME); arch=$(dpkg --print-architecture)
    allowed=$(setup_manifest_value apt_architectures)
    [[ -n $allowed && $distro == "$(setup_manifest_value distribution)" &&
        $version == "$(setup_manifest_value version)" && $codename == "$(setup_manifest_value codename)" &&
        $allowed == *\""$arch"\"* ]] || {
        printf 'UNSUPPORTED Hyprland target: %s %s (%s), %s. No changes made.\n' "$distro" "$version" "$codename" "$arch" >&2; return 4;
    }
    printf 'Target: %s %s (%s), %s\n' "$distro" "$version" "$codename" "$arch"
}
setup_safe_path() {
    local path=$1 cursor=$1
    [[ $path == /* && $path != *$'\n'* && $path != *$'\r'* && $path != *$'\t'* &&
        /$path/ != *'/../'* && /$path/ != *'/./'* ]] || { setup_error 'Unsafe absolute path.'; return 2; }
    while [[ $cursor != / ]]; do
        [[ ! -L $cursor ]] || { setup_error 'Refusing a symlink in a managed path.'; return 2; }
        cursor=${cursor%/*}; [[ -n $cursor ]] || cursor=/
    done
}
setup_identity() {
    local record name password uid gid gecos shell group_record value index
    if [[ -n ${SUDO_USER-} && ${SUDO_USER} != root ]]; then
        [[ -n ${SUDO_UID-} && -n ${SUDO_GID-} ]] || { setup_error 'Incomplete sudo identity.'; return 2; }
        target_user=$SUDO_USER
        [[ -z $requested_user || $requested_user == "$target_user" ]] || { setup_error '--user conflicts with sudo caller.'; return 2; }
    elif [[ $(id -u) == 0 ]]; then
        [[ -n $requested_user ]] || { setup_error 'Direct root invocation requires --user NAME.'; return 2; }
        target_user=$requested_user
    else
        target_user=${requested_user:-$(id -un)}
        [[ $target_user == "$(id -un)" ]] || { setup_error 'An unprivileged plan can inspect only the current user.'; return 2; }
    fi
    [[ $target_user =~ ^[a-zA-Z_][a-zA-Z0-9_.-]*\$?$ ]] || { setup_error 'Invalid target user.'; return 2; }
    record=$(getent passwd "$target_user") || { setup_error 'Unknown target user.'; return 2; }
    [[ $record != *$'\n'* ]] || { setup_error 'Ambiguous passwd entry.'; return 2; }
    IFS=: read -r name password uid gid gecos target_home shell <<< "$record"
    [[ $name == "$target_user" && $uid =~ ^[0-9]+$ && $gid =~ ^[0-9]+$ && $uid != 0 &&
        $(id -u "$target_user") == "$uid" && $(id -g "$target_user") == "$gid" ]] || { setup_error 'Target UID/GID mismatch or root target.'; return 2; }
    group_record=$(getent group "$gid") || { setup_error 'Unknown primary group.'; return 2; }
    [[ ${group_record%%:*} != '' ]] || return 2
    if [[ -n ${SUDO_USER-} && ${SUDO_USER} != root ]]; then
        [[ ${SUDO_UID} == "$uid" && ${SUDO_GID} == "$gid" ]] || { setup_error 'SUDO_UID/SUDO_GID do not match passwd.'; return 2; }
    fi
    case ${shell##*/} in false|nologin|'') setup_error "Target account needs an interactive login shell."; return 2 ;; esac
    setup_safe_path "$target_home" || return
    [[ -d $target_home && $target_home != / && $target_home != /root && $(stat -c %u -- "$target_home") == "$uid" ]] || { setup_error 'HOME must exist and be owned by the target user.'; return 2; }
    local -A resolved=([--xdg-config-home]="$target_home/.config" [--xdg-data-home]="$target_home/.local/share"
        [--xdg-state-home]="$target_home/.local/state" [--xdg-cache-home]="$target_home/.cache")
    for ((index=0; index<${#xdg_arguments[@]}; index+=2)); do
        value=${xdg_arguments[index+1]%/}
        [[ $value == "$target_home/"* ]] || { setup_error 'XDG path must be inside verified HOME.'; return 2; }
        resolved[${xdg_arguments[index]}]=$value
    done
    local option cursor
    xdg_arguments=()
    for option in --xdg-config-home --xdg-data-home --xdg-state-home --xdg-cache-home; do
        value=${resolved[$option]}; setup_safe_path "$value" || return
        cursor=$value
        while [[ $cursor == "$target_home/"* ]]; do
            if [[ -e $cursor && ( ! -d $cursor || $(stat -c %u -- "$cursor") != "$uid" ) ]]; then
                setup_error 'Existing XDG directory has an unsafe type or owner.'; return 2
            fi
            cursor=${cursor%/*}
        done
        xdg_arguments+=("$option" "$value")
    done
    forwarded=(--user "$target_user" "${xdg_arguments[@]}")
    printf 'User: %s (uid=%s gid=%s), HOME=%s\n' "$target_user" "$uid" "$gid" "$target_home"
}
setup_preliminary_plan() {
    printf 'Hyprland pin: %s\n' "$(setup_manifest_value version_pin)"
    awk '/^base_packages =/{selected=1} /^\[hyprland.backport_packages\]/{selected=1} /^\[unsupported\]/{selected=0} selected{print}' "$setup_root/manifests/platforms.toml"
    cat <<'PLAN'
Preliminary plan (Python absent; no APT metadata refreshed):
  1. Verify signed Debian sources; install missing python3/ca-certificates.
  2. Refresh isolated signed Debian/backports metadata; resolve full package set.
  3. Refuse removals/downgrades; install Hyprland and manifest integrations.
  4. As target user: prepare pinned uv/Python 3.14.7 and locked runtime.
  5. As target user: back up and apply managed configuration/session launcher.
Preserve network, locale, timezone and login shell. No session is started.
Availability/signatures require the explicitly authorized APT refresh during apply.
PLAN
}
setup_source_access() {
    local path source_uid source_gid
    local -a required=(bootstrap.py uv.lock pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md
        manifests/platforms.toml manifests/fonts.toml assets/hyprland/profile.lua
        assets/fonts/60-setup-system-fonts.conf src/setup_system/installation.py
        src/setup_system/planning.py src/setup_system/storage.py src/setup_system/user_install.py
        src/setup_system/adapters/debian/hyprland.py)
    # The unprivileged wheel build also needs every bundled source/resource file.
    while IFS= read -r -d '' path; do
        required+=("${path#"$setup_root/"}")
    done < <(find "$setup_root/src" "$setup_root/assets" "$setup_root/manifests" -type f -print0)
    if [[ $(id -u) == 0 ]]; then
        command -v setpriv >/dev/null 2>&1 || {
            setup_error 'Source preflight requires Debian util-linux setpriv; no changes made.'; return 2;
        }
        source_uid=$(id -u "$target_user"); source_gid=$(id -g "$target_user")
        [[ $source_uid =~ ^[0-9]+$ && $source_uid != 0 && $source_gid =~ ^[0-9]+$ ]] || {
            setup_error 'Source preflight requires the verified non-root account.'; return 2;
        }
        env -i PATH="$PATH" LANG=C setpriv --reuid "$source_uid" --regid "$source_gid" --init-groups \
            /usr/bin/test -x "$setup_root" || {
            setup_error 'Source directory is not accessible to the target user; no changes made.'; return 2;
        }
        for path in "${required[@]}"; do
            env -i PATH="$PATH" LANG=C setpriv --reuid "$source_uid" --regid "$source_gid" --init-groups \
                /usr/bin/test -r "$setup_root/$path" || {
                setup_error "Source file is not readable by the target user: $path. No changes made."; return 2;
            }
        done
    else
        [[ -x $setup_root ]] || { setup_error 'Source directory is not accessible.'; return 2; }
        for path in "${required[@]}"; do
            [[ -r $setup_root/$path ]] || { setup_error "Source file is not readable: $path."; return 2; }
        done
    fi
}
setup_python_prerequisite() (
    # Only entered after consent. Debian's installed keyring is the trust anchor.
    [[ -f /usr/share/keyrings/debian-archive-keyring.gpg && ! -L /usr/share/keyrings/debian-archive-keyring.gpg ]] || { setup_error 'Debian archive keyring is required.'; return 2; }
    stage=$(mktemp -d /run/setup-system-bootstrap.XXXXXXXX)
    trap 'rm -rf -- "$stage"' EXIT
    mkdir -p "$stage/lists/partial" "$stage/archives/partial"
    chmod 755 "$stage" "$stage/lists" "$stage/archives"
    printf 'Types: deb\nURIs: http://deb.debian.org/debian\nSuites: %s\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n' "$(setup_manifest_value codename)" > "$stage/debian.sources"
    apt_options=(-o "Dir::Etc::sourcelist=$stage/debian.sources" -o Dir::Etc::sourceparts=-
        -o "Dir::State::lists=$stage/lists" -o "Dir::Cache::archives=$stage/archives"
        -o Acquire::AllowInsecureRepositories=false -o APT::Get::AllowUnauthenticated=false
        -o Acquire::Retries=0 -o Acquire::http::Timeout=30 -o DPkg::Lock::Timeout=30)
    apt_command=(/usr/bin/timeout --kill-after=15s 900 /usr/bin/env -i
        PATH="$PATH" LANG=C LC_ALL=C DEBIAN_FRONTEND=noninteractive /usr/bin/apt-get)
    printf 'Stage: bootstrap signed Debian Python prerequisites\n'
    "${apt_command[@]}" "${apt_options[@]}" -o APT::Update::Error-Mode=any update || return 5
    "${apt_command[@]}" "${apt_options[@]}" --simulate --no-remove install python3 ca-certificates || return 5
    "${apt_command[@]}" "${apt_options[@]}" --yes --no-remove --no-install-recommends install python3 ca-certificates || return 5
)
setup_main() {
    local setup_root
    setup_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
    setup_parse "$@" || return
    if [[ $mode == help ]]; then setup_usage; return 0; fi
    setup_platform || return
    setup_identity || return
    [[ -r $setup_root/bootstrap.py && -r $setup_root/uv.lock && -r $setup_root/pyproject.toml && -r $setup_root/src/setup_system/user_install.py && -r $setup_root/assets/hyprland/profile.lua ]] || { setup_error 'Incomplete local source bundle.'; return 2; }
    setup_source_access || return
    if command -v python3 >/dev/null 2>&1; then
        local yes_arguments=(); [[ $consent == 0 ]] || yes_arguments=(--yes)
        exec env -i PATH="$PATH" HOME="$target_home" LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
            python3 -I -B "$setup_root/bootstrap.py" "$mode" "${forwarded[@]}" "${yes_arguments[@]}"
    fi
    setup_preliminary_plan
    [[ $mode == apply ]] || return 0
    [[ $(id -u) == 0 ]] || { setup_error 'Installation requires sudo bash ./setup.sh.'; return 2; }
    if [[ $consent == 0 ]]; then
        [[ -t 0 ]] || { setup_error 'No TTY: apply requires explicit --yes consent.'; return 2; }
        local answer
        read -r -p 'Apply the complete plan? [y/N] ' answer
        [[ $answer == y || $answer == yes || $answer == Y ]] || return 2
    fi
    setup_python_prerequisite || { printf 'FAILED: prerequisites. Fix APT/network and re-run the same command.\n' >&2; return 5; }
    exec env -i PATH="$PATH" HOME="$target_home" LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
        python3 -I -B "$setup_root/bootstrap.py" apply "${forwarded[@]}" --yes
}
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then setup_main "$@"; fi
