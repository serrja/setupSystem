# Thin interactive integration: the installed CLI owns the catalog and picker.
projectCd() {
    emulate -L zsh
    if (( ! $+commands[setup-system] )); then
        print -u2 -- 'setup-system is unavailable'
        return 127
    fi
    local destination
    destination=$(command setup-system projects path "$@") || return $?
    [[ -n $destination ]] || return 0
    builtin cd -- "$destination"
}
