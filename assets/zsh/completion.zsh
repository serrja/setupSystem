autoload -Uz compinit
if compinit; then
    codex_completion="${XDG_CACHE_HOME:-$HOME/.cache}/setup-system/codex.zsh"
    [[ -r $codex_completion ]] && source "$codex_completion"
    unset codex_completion
fi
