autoload -Uz compinit
# -D keeps startup read-only; Codex completion is generated only by explicit apply.
if compinit -D; then
    codex_completion="${XDG_CACHE_HOME:-$HOME/.cache}/setup-system/codex.zsh"
    [[ -r $codex_completion ]] && source "$codex_completion"
    unset codex_completion
fi
