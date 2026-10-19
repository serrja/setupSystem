if [[ -z ${HISTFILE:-} ]]; then
    if [[ -f $HOME/.zsh_history ]]; then
        # Preserve an existing history file until its owner moves it explicitly.
        HISTFILE=$HOME/.zsh_history
    else
        HISTFILE=${XDG_STATE_HOME:-$HOME/.local/state}/zsh/history
    fi
fi
HISTSIZE=5000
SAVEHIST=10000
setopt APPEND_HISTORY HIST_IGNORE_DUPS HIST_REDUCE_BLANKS NO_PROMPT_SUBST
