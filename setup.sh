#!/usr/bin/env bash
# Canonical clean-Debian entrypoint. Never source this from shell startup.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

setup_error() { printf 'setupSystem: %s\n' "$*" >&2; return 2; }
setup_usage() {
    cat <<'HELP'
Usage: sudo bash ./setup.sh [apply] [--yes] [--user NAME] [--vpn-config-file PATH]
       bash ./setup.sh {plan|--dry-run|--help}
       [--xdg-config-home PATH] [--xdg-data-home PATH]
       [--xdg-state-home PATH] [--xdg-cache-home PATH]
No arguments install Hyprland for the verified sudo caller. Direct root needs --user.
XDG paths must be inside that user's HOME; explicit options work without sudo -E.
Help/plan never install prerequisites, download files or refresh APT metadata.
Without Python, plan shows preliminary shell preflight. Apply prepares missing Python/CA.
One confirmation authorizes downloads, offline package/user apply and native-service activation.
Interactive apply requests an HTTPS subscription with hidden input after consent.
Unattended --yes requires --vpn-config-file: a private user-owned file inside HOME.
Pass only the private filename, never a subscription URL as a command argument.
The temporary client/config is removed before offline package/user apply.
Native Happ helper activation is a separate operation outside offline namespaces.
Temporary input supports plain/base64 VLESS or compatible JSON with TLS/REALITY.
HTTPS alone does not guarantee compatible content; encrypted Happ payloads are unsupported.
Initial client/subscription access is required; the VPN cannot download itself.
HELP
}
setup_parse() {
    mode=apply; consent=0; requested_user=''; vpn_file=''; xdg_arguments=(); local verb='' want_help=0
    local -A seen=()
    while (($#)); do
        case "$1" in
            -h|--help) want_help=1; shift ;;
            apply|plan|--dry-run)
                [[ -z $verb ]] || { setup_error 'Choose only one apply/plan mode.'; return 2; }
                verb=$1; [[ $1 == apply ]] && mode=apply || mode=plan; shift ;;
            --yes) consent=1; shift ;;
            --user|--vpn-config-file|--xdg-config-home|--xdg-data-home|--xdg-state-home|--xdg-cache-home)
                (($# >= 2)) && [[ -n $2 && $2 != --* ]] || { setup_error "Missing value for $1."; return 2; }
                if [[ $1 == --user ]]; then
                    [[ -z $requested_user ]] || { setup_error 'Duplicate --user.'; return 2; }
                    requested_user=$2
                elif [[ $1 == --vpn-config-file ]]; then
                    [[ -z $vpn_file ]] || { setup_error 'Duplicate --vpn-config-file.'; return 2; }
                    vpn_file=$2
                else
                    [[ -z ${seen[$1]-} ]] || { setup_error "Duplicate $1."; return 2; }
                    seen[$1]=1; xdg_arguments+=("$1" "$2"); fi
                shift 2 ;;
            *) setup_error "Unknown or retired argument. Use --help; private URLs are not arguments."; return 2 ;;
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
setup_systemctl() {
    /usr/bin/timeout --kill-after=1s 15 /usr/bin/env -i \
        PATH="$PATH" LANG=C LC_ALL=C /usr/bin/systemctl "$@"
}
setup_init() {
    local init required client manager client_pattern manager_pattern
    init=$(setup_manifest_value init_system)
    required=$(setup_manifest_value init_version)
    [[ $init == systemd && $required =~ ^[0-9]+$ ]] || {
        setup_error 'Unsupported init requirement in the Hyprland profile.'; return 2;
    }
    client_pattern="^systemd $required([ .(]|$)"
    manager_pattern="^$required([.~-]|$)"
    if ! client=$(setup_systemctl --version 2>/dev/null) || [[ ! $client =~ $client_pattern ]]; then
        setup_error "Hyprland/Happ requires the reviewed systemd $required client."; return 2;
    fi
    if ! manager=$(setup_systemctl --system --no-pager --no-ask-password show --property=Version --value 2>/dev/null) ||
        [[ ! $manager =~ $manager_pattern ]]; then
        setup_error "Hyprland/Happ requires an accessible systemd $required system manager as init."; return 2;
    fi
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
    setup_init || return
    printf 'Target: %s %s (%s), %s\n' "$distro" "$version" "$codename" "$arch"
}
setup_safe_path() {
    local path=$1 cursor=$1
    [[ $path == /* && ! $path =~ [[:cntrl:]] &&
        /$path/ != *'/../'* && /$path/ != *'/./'* ]] || { setup_error 'Unsafe absolute path.'; return 2; }
    while [[ $cursor != / ]]; do
        [[ ! -L $cursor ]] || { setup_error 'Refusing a symlink in a managed path.'; return 2; }
        cursor=${cursor%/*}; [[ -n $cursor ]] || cursor=/
    done
}
setup_managed_path() {
    local kind=$1 relative=$2 value cursor
    case $kind in config|data|state|cache) ;; *) setup_error 'Invalid managed path kind.'; return 2 ;; esac
    [[ $relative != /* ]] || { setup_error 'Invalid managed relative path.'; return 2; }
    value="${resolved[--xdg-$kind-home]}/${relative%/}"
    setup_safe_path "$value" || return
    if [[ -e $value ]]; then
        if [[ $relative == */ ]]; then
            [[ -d $value ]] || { setup_error 'Managed directory has an unsafe type.'; return 2; }
        else
            [[ -f $value ]] || { setup_error 'Managed file has an unsafe type.'; return 2; }
        fi
    fi
    cursor=$value
    while [[ $cursor == "$target_home/"* ]]; do
        [[ ! -e $cursor || $(stat -c %u -- "$cursor") == "$uid" ]] || {
            setup_error 'Managed path has a different owner.'; return 2;
        }
        [[ $cursor == "$value" || ! -e $cursor || -d $cursor ]] || {
            setup_error 'Managed parent is not a directory.'; return 2;
        }
        cursor=${cursor%/*}
    done
}
setup_managed_preflight() {
    local kind relative asset font_version font_name
    while IFS=: read -r kind relative; do
        setup_managed_path "$kind" "$relative" || return
    done < <(awk -F'"' '
        /^\[hyprland\]$/ {selected=1;next} /^\[/ {selected=0}
        selected && /^preflight_paths = \[/ {reading=1;next}
        reading && /^\]/ {exit} reading && NF>=3 {print $2}' "$setup_root/manifests/platforms.toml")
    while IFS= read -r -d '' asset; do
        [[ ${asset##*/} == start-hyprland ]] && continue
        setup_managed_path config "hypr/setup-system/${asset#"$setup_root/assets/hyprland/"}" || return
    done < <(find "$setup_root/assets/hyprland" -type f -print0)
    font_version=$(awk -F'"' '/^\[jetbrains_mono_nerd_font\]$/{selected=1;next} selected && /^version = /{print $2;exit}' "$setup_root/manifests/fonts.toml")
    [[ -n $font_version ]] || { setup_error 'Missing font manifest version.'; return 2; }
    setup_managed_path data "fonts/JetBrainsMonoNerdFont-$font_version/" || return
    while IFS= read -r font_name; do
        setup_managed_path data "fonts/JetBrainsMonoNerdFont-$font_version/$font_name" || return
    done < <(awk -F'"' '/^name = / {print $2}' "$setup_root/manifests/fonts.toml")
}

setup_subscription_file() {
    [[ -n $vpn_file ]] || return 0
    setup_safe_path "$vpn_file" >/dev/null 2>&1 || {
        setup_error 'VPN input file path is unsafe.'; return 2;
    }
    [[ $vpn_file == "$target_home/"* && -f $vpn_file ]] || {
        setup_error 'VPN input must be a regular private file inside verified HOME.'; return 2;
    }
    local file_uid file_mode links size
    if ! file_uid=$(stat -c %u -- "$vpn_file" 2>/dev/null) ||
        ! file_mode=$(stat -c %a -- "$vpn_file" 2>/dev/null) ||
        ! links=$(stat -c %h -- "$vpn_file" 2>/dev/null) ||
        ! size=$(stat -c %s -- "$vpn_file" 2>/dev/null); then
        setup_error 'VPN input file metadata is unavailable.'; return 2;
    fi
    [[ $file_uid == "$uid" && $file_mode =~ ^[0-7]{3,4}$ && $links == 1 &&
        $size =~ ^[0-9]+$ ]] &&
        (( (8#$file_mode & 077) == 0 && size > 0 && size <= 8192 )) || {
        setup_error 'VPN input must be private, user-owned, single-link and bounded to 1..8192 bytes.'; return 2;
    }
}
setup_has_tty() { [[ -t 0 ]]; }

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
    setup_subscription_file || return
    setup_managed_preflight || return
    printf 'User: %s (uid=%s gid=%s), HOME=%s\n' "$target_user" "$uid" "$gid" "$target_home"
}
setup_preliminary_plan() {
    printf 'Hyprland pin: %s\n' "$(setup_manifest_value version_pin)"
    awk '/^base_packages =/{selected=1} /^\[hyprland.backport_packages\]/{selected=1} /^\[unsupported\]/{selected=0} selected{print}' "$setup_root/manifests/platforms.toml"
    cat <<'PLAN'
Preliminary plan (Python/CA bootstrap required; no APT metadata refreshed):
  1. After consent, bootstrap missing Python/CA from signed Debian sources.
  2. As target user: hidden HTTPS subscription, temporary pinned Xray loopback proxy.
  3. Download/verify Happ, signed Debian/backports packages, locked runtime and fonts.
  4. Stop/remove temporary client/config; install cached packages offline with service deferral.
  5. Outside offline namespaces, start native Happ helper; preserve a running instance.
  6. As target user: offline backed-up configuration; final Happ import in its GUI after login.
Preserve existing network configuration, locale, timezone and login shell. No GUI is started.
Initial Debian/subscription reachability is required; Xray needs access or a verified seed.
Availability/signatures require the explicitly authorized APT refresh during apply.
PLAN
}
setup_source_access() {
    local path source_uid source_gid
    local -a required=(src/setup_system/_installer_entry.py uv.lock pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md
        manifests/platforms.toml manifests/fonts.toml manifests/network.toml manifests/happ.toml
        src/setup_system/network_worker.py src/setup_system/network_session.py
        src/setup_system/network.py src/setup_system/diagnostics.py assets/hyprland/profile.lua
        assets/fonts/60-setup-system-fonts.conf src/setup_system/installation.py
        src/setup_system/planning.py src/setup_system/storage.py src/setup_system/runtime.py
        src/setup_system/entrypoint.py src/setup_system/application/installer.py
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
setup_ca_ready() {
    local ca_status
    ca_status=$(/usr/bin/env -i PATH="$PATH" LANG=C LC_ALL=C /usr/bin/dpkg-query \
        --show '--showformat=${db:Status-Status}' ca-certificates 2>/dev/null) || return 1
    [[ $ca_status == installed && -f /etc/ssl/certs/ca-certificates.crt &&
        ! -L /etc/ssl/certs/ca-certificates.crt && -r /etc/ssl/certs/ca-certificates.crt &&
        -s /etc/ssl/certs/ca-certificates.crt ]]
}
setup_prerequisite_files() {
    local stage=$1
    chmod 755 "$stage"
    local folder
    for folder in lists lists/partial archives archives/partial; do
        mkdir -p "$stage/$folder"
        chmod 755 "$stage/$folder"
    done
    printf 'Types: deb\nURIs: http://deb.debian.org/debian\nSuites: %s\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.pgp\n' "$(setup_manifest_value codename)" > "$stage/debian.sources"
    chmod 644 "$stage/debian.sources"
}
setup_python_prerequisite() (
    # Only entered after consent. Debian's installed keyring is the trust anchor.
    [[ -f /usr/share/keyrings/debian-archive-keyring.pgp && ! -L /usr/share/keyrings/debian-archive-keyring.pgp ]] || { setup_error 'Debian archive keyring is required.'; return 2; }
    stage=$(mktemp -d /run/setup-system-prerequisites.XXXXXXXX)
    trap 'rm -rf -- "$stage"' EXIT
    setup_prerequisite_files "$stage"
    apt_options=(-o "Dir::Etc::sourcelist=$stage/debian.sources" -o Dir::Etc::sourceparts=-
        -o "Dir::State::lists=$stage/lists" -o "Dir::Cache::archives=$stage/archives"
        -o Acquire::AllowInsecureRepositories=false -o APT::Get::AllowUnauthenticated=false
        -o Acquire::Retries=0 -o Acquire::http::Timeout=30 -o DPkg::Lock::Timeout=30)
    apt_command=(/usr/bin/timeout --kill-after=15s 900 /usr/bin/env -i
        PATH="$PATH" LANG=C LC_ALL=C DEBIAN_FRONTEND=noninteractive /usr/bin/apt-get)
    printf 'Stage: signed Debian Python/CA prerequisites\n'
    "${apt_command[@]}" "${apt_options[@]}" -o APT::Update::Error-Mode=any update || return 5
    "${apt_command[@]}" "${apt_options[@]}" --simulate --no-remove install python3 ca-certificates || return 5
    "${apt_command[@]}" "${apt_options[@]}" --yes --no-remove --no-install-recommends install python3 ca-certificates || return 5
    if ! setup_ca_ready; then
        [[ -x /usr/sbin/update-ca-certificates && ! -L /usr/sbin/update-ca-certificates ]] || {
            setup_error 'CA bundle is unavailable and the Debian repair tool is missing.' || return 5;
        }
        printf 'Stage: rebuild the missing Debian CA bundle (preserve configured trust)\n'
        /usr/bin/timeout --kill-after=15s 120 /usr/bin/env -i PATH="$PATH" LANG=C LC_ALL=C \
            /usr/sbin/update-ca-certificates || return 5
    fi
    setup_ca_ready || { setup_error 'CA bundle is still unavailable; repair Debian trust before retrying.' || return 5; }
)
setup_handoff() {
    exec env -i PATH="$PATH" HOME="$target_home" LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
        python3 -I -B "$setup_root/src/setup_system/_installer_entry.py" \
        "$mode" "$target_user" "${xdg_arguments[1]}" "${xdg_arguments[3]}" \
        "${xdg_arguments[5]}" "${xdg_arguments[7]}" "$1" "${vpn_file:--}"
}
setup_main() {
    local setup_root
    setup_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
    setup_parse "$@" || return
    if [[ $mode == help ]]; then setup_usage; return 0; fi
    setup_platform || return
    setup_identity || return
    [[ -r $setup_root/src/setup_system/_installer_entry.py && -r $setup_root/uv.lock && -r $setup_root/pyproject.toml && -r $setup_root/src/setup_system/runtime.py && -r $setup_root/assets/hyprland/profile.lua ]] || { setup_error 'Incomplete local source bundle.'; return 2; }
    setup_source_access || return
    if [[ $mode == apply ]] && ! setup_has_tty; then
        [[ $consent == 1 ]] || { setup_error 'No TTY: apply requires explicit --yes consent.'; return 2; }
        [[ -n $vpn_file ]] || { setup_error 'No TTY: temporary VPN requires a private --vpn-config-file.'; return 2; }
    fi
    local has_python=0 has_ca=0
    if command -v python3 >/dev/null 2>&1; then has_python=1; fi
    if setup_ca_ready; then has_ca=1; fi
    if [[ $has_python == 1 ]]; then
        if [[ $mode == plan ]]; then
            [[ $has_ca == 1 ]] || printf 'Prerequisite plan: missing CA certificates/bundle; signed Debian bootstrap before HTTPS downloads.\n'
            setup_handoff "$consent"
        elif [[ $has_ca == 1 ]]; then
            setup_handoff "$consent"
        fi
    fi
    setup_preliminary_plan
    [[ $mode == apply ]] || return 0
    [[ $(id -u) == 0 ]] || { setup_error 'Installation requires sudo bash ./setup.sh.'; return 2; }
    if [[ $consent == 0 ]]; then
        setup_has_tty || { setup_error 'No TTY: apply requires explicit --yes consent.'; return 2; }
        local answer
        read -r -p 'Apply the complete plan? [y/N] ' answer
        [[ $answer == y || $answer == yes || $answer == Y ]] || return 2
    fi
    setup_python_prerequisite || { printf 'FAILED: prerequisites. Fix APT/network and re-run the same command.\n' >&2; return 5; }
    setup_handoff 1
}
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then setup_main "$@"; fi
