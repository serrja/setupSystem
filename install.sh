#!/bin/sh
# Deliver a versioned CLI as the real user; all system setup belongs to Textual/Typer.
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

fail() { printf '%s\n' "$*" >&2; exit 2; }
usage() {
    printf '%s\n' 'Usage: sh install.sh --release vX.Y.Z [--user NAME]' \
        'Downloads a verified release and pinned user runtime, then opens Textual.' \
        'Requires Debian, CA trust, curl, tar and an interactive terminal.' \
        'Direct root login requires --user; no Python builds run as root.'
}
as_user() {
    if [ "$(id -u)" -eq 0 ]; then
        runuser -u "$target" -- env -i HOME="$target_home" USER="$target" LOGNAME="$target" \
            PATH=/usr/sbin:/usr/bin:/sbin:/bin \
            UV_PYTHON_INSTALL_DIR="$target_home/.local/share/setup-system/python" \
            UV_PYTHON_BIN_DIR="$target_home/.local/share/setup-system/bin" \
            UV_CACHE_DIR="$target_home/.cache/setup-system/uv" "$@"
    else
        env -i HOME="$target_home" USER="$target" LOGNAME="$target" \
            PATH=/usr/sbin:/usr/bin:/sbin:/bin \
            UV_PYTHON_INSTALL_DIR="$target_home/.local/share/setup-system/python" \
            UV_PYTHON_BIN_DIR="$target_home/.local/share/setup-system/bin" \
            UV_CACHE_DIR="$target_home/.cache/setup-system/uv" "$@"
    fi
}
fetch() { as_user curl --fail --location --proto '=https' --proto-redir '=https' \
    --connect-timeout 15 --max-time 300 --max-filesize "${3:-33554432}" --user-agent setupSystem --output "$2" "$1"; }
policy_value() {
    value=$(awk -F '"' -v key="$1" '$1 ~ "^" key " = $" { print $2 }' "$policy")
    [ -n "$value" ] && [ "$(printf '%s\n' "$value" | wc -l)" -eq 1 ] || fail 'Invalid runtime policy'
    printf '%s\n' "$value"
}
bootstrap_main() {
    release= target=
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --help|-h) usage; return 0 ;;
            --release|--user)
                option=$1; shift
                [ "$#" -gt 0 ] && [ -n "$1" ] || fail "Missing value for $option"
                case "$option" in --release) release=$1 ;; --user) target=$1 ;; esac ;;
            *) fail "Unknown bootstrap argument: $1" ;;
        esac
        shift
    done
    printf '%s\n' "$release" | /bin/grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+(-dev\.[0-9]+)?$' || fail 'Use an explicit versioned release'
    [ "$(uname -s)" = Linux ] && [ "$(awk -F= '$1 == "ID" {gsub(/"/, "", $2); print $2}' /etc/os-release)" = debian ] || fail 'This bootstrap requires Debian'
    if [ -n "${SUDO_USER-}" ]; then
        [ -z "$target" ] || [ "$target" = "$SUDO_USER" ] || fail 'Explicit user differs from sudo caller'
        target=$SUDO_USER
        [ "${SUDO_UID-}" = "$(id -u "$target")" ] && \
            [ "${SUDO_GID-}" = "$(id -g "$target")" ] || fail 'Sudo identity mismatch'
    fi
    if [ "$(id -u)" -eq 0 ]; then
        [ -n "$target" ] || fail 'Direct root login requires --user NAME'
    else
        current=$(id -un)
        [ -z "$target" ] || [ "$target" = "$current" ] || fail 'Use the current normal user'
        target=$current
        command -v sudo >/dev/null || fail 'Use root with --user when sudo is unavailable'
    fi
    printf '%s\n' "$target" | /bin/grep -Eq '^[a-z_][a-z0-9_-]*[$]?$' || fail 'Invalid target user'
    [ "$(id -u "$target")" -ne 0 ] || fail 'Root is not a desktop user'
    record=$(getent passwd "$target") || fail 'Unknown target user'
    target_home=$(printf '%s\n' "$record" | cut -d: -f6)
    case "$target_home" in /*) ;; *) fail 'Target HOME must be absolute' ;; esac
    [ -d "$target_home" ] && [ ! -L "$target_home" ] && \
        [ "$(stat -c %u "$target_home")" = "$(id -u "$target")" ] || fail 'Unsafe target HOME'
    for relative in .local .local/share .local/share/setup-system \
        .local/share/setup-system/bin .local/share/setup-system/python \
        .local/share/setup-system/runtime .local/share/setup-system/releases \
        .cache .cache/setup-system .cache/setup-system/uv; do
        path=$target_home/$relative
        [ ! -L "$path" ] || fail 'Managed runtime paths must not traverse symlinks'
        if [ -e "$path" ]; then
            [ -d "$path" ] && [ "$(stat -c %u "$path")" = "$(id -u "$target")" ] || fail 'Unsafe managed runtime directory'
        fi
    done
    for tool in curl tar sha256sum awk mktemp runuser; do
        command -v "$tool" >/dev/null || fail "Missing bootstrap prerequisite: $tool"
    done
    [ -t 1 ] && ( : </dev/tty ) 2>/dev/null || fail 'An interactive terminal is required'
    case "$(uname -m)" in x86_64) architecture=amd64 ;; aarch64|arm64) architecture=arm64 ;; *) fail 'Unsupported architecture' ;; esac
    # The private user-owned workspace is never a privileged extraction destination.
    workspace=$(as_user mktemp -d /tmp/setup-system-delivery.XXXXXXXX)
    trap 'as_user rm -rf -- "$workspace"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM HUP
    policy=$workspace/runtime.toml
    fetch "https://raw.githubusercontent.com/serrja/setupSystem/$release/manifests/runtime.toml" "$policy" 8192
    python=$(policy_value python); uv=$(policy_value uv)
    uv_base=$(policy_value uv_base); uv_target=$(policy_value "${architecture}_target")
    uv_hash=$(policy_value "${architecture}_sha256")
    for version in "$python" "$uv"; do
        printf '%s\n' "$version" | /bin/grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || fail 'Invalid runtime version'
    done
    [ "$uv_base" = 'https://releases.astral.sh/github/uv/releases/download' ] || fail 'Unexpected runtime origin'
    printf '%s\n' "$uv_hash" | /bin/grep -Eq '^[0-9a-f]{64}$' || fail 'Invalid runtime checksum'
    case "$uv_target" in x86_64-unknown-linux-gnu|aarch64-unknown-linux-gnu) ;; *) fail 'Invalid runtime architecture' ;; esac
    uv_command=$target_home/.local/share/setup-system/bin/uv
    if [ -f "$uv_command" ] && [ ! -L "$uv_command" ] && \
        [ "$(as_user "$uv_command" --version)" = "uv $uv" ]; then
        : # Reuse the existing pinned user runtime tool.
    else
        fetch "$uv_base/$uv/uv-$uv_target.tar.gz" "$workspace/uv.tar.gz" 83886080
        printf '%s  %s\n' "$uv_hash" "$workspace/uv.tar.gz" | sha256sum --check --status || fail 'Runtime checksum mismatch'
        as_user tar -xzf "$workspace/uv.tar.gz" -C "$workspace" --no-same-owner --no-same-permissions "$uv_target/uv"
        uv_command=$workspace/$uv_target/uv
    fi
    native=$(printf '%s' "${release#v}" | sed 's/-dev\./.dev/')
    base=https://github.com/serrja/setupSystem/releases/download/$release
    fetch "$base/checksums.sha256" "$workspace/checksums.sha256" 32768
    archive=setup_system-$native.tar.gz
    fetch "$base/$archive" "$workspace/$archive"
    expected=$(awk -v file="$archive" '$2 == file { print $1 }' "$workspace/checksums.sha256")
    printf '%s\n' "$expected" | /bin/grep -Eq '^[0-9a-f]{64}$' || fail 'Invalid release checksum'
    printf '%s  %s\n' "$expected" "$workspace/$archive" | sha256sum --check --status || fail 'Release checksum mismatch'
    # All downloads/builds and their cache files remain owned by the desktop user.
    as_user "$uv_command" python install "$python"
    as_user "$uv_command" run --no-project --python "$python" --no-config -- python -I -B - \
        "$workspace/$archive" "$workspace/source" <<'PYTHON'
import pathlib
import sys
import tarfile

with tarfile.open(sys.argv[1]) as bundle:
    members = bundle.getmembers()
    names = [str(pathlib.PurePosixPath(item.name)).casefold() for item in members]
    if len(members) > 4096 or sum(item.size for item in members) > 32 * 1024 * 1024:
        raise SystemExit("Release archive exceeds its limits")
    if len(names) != len(set(names)) or any(
        not (item.isfile() or item.isdir())
        or item.size < 0
        or pathlib.PurePosixPath(item.name).is_absolute()
        or ".." in pathlib.PurePosixPath(item.name).parts
        or "\\" in item.name
        for item in members
    ):
        raise SystemExit("Unsafe release archive")
    bundle.extractall(pathlib.Path(sys.argv[2]), filter="data")
PYTHON
    source=$workspace/source/setup_system-$native
    [ -d "$source/src/setup_system" ] && [ -f "$source/uv.lock" ] || fail 'Incomplete release source'
    as_user "$uv_command" run --no-project --python "$python" --no-config -- python -I -B \
        "$source/src/setup_system/_delivery_entry.py" "$source" "$release" "$uv_command"
    source=$target_home/.local/share/setup-system/releases/$release
    executable=$target_home/.local/share/setup-system/runtime/bin/python
    as_user rm -rf -- "$workspace"
    trap - EXIT
    exec </dev/tty
    if [ "$(id -u)" -eq 0 ]; then
        exec "$executable" -I -B -m setup_system --source "$source" --user "$target"
    fi
    exec sudo -- "$executable" -I -B -m setup_system --source "$source"
}
bootstrap_main "$@"
