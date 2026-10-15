typeset -U path
path=( "$HOME/.local/bin" $path )
if [[ -z ${EDITOR:-} ]]; then
    if (( $+commands[nvim] )); then export EDITOR=nvim
    elif (( $+commands[vim] )); then export EDITOR=vim
    else export EDITOR=nano
    fi
fi
[[ -z ${VISUAL:-} ]] && export VISUAL=$EDITOR
