if [[ -d $HOME/.local/opt/zsh/usr/share/zsh/functions ]]; then
    module_path=( $HOME/.local/opt/zsh/usr/lib/*/zsh/*(N/) $module_path )
    fpath=( $HOME/.local/opt/zsh/usr/share/zsh/functions/**/*(N/) )
fi
typeset -U path
path=( "$HOME/.local/bin" $path )
if (( $+commands[nvim] )); then
    export EDITOR=nvim VISUAL=nvim
elif [[ -z ${EDITOR:-} ]]; then
    if (( $+commands[vim] )); then export EDITOR=vim
    else export EDITOR=nano
    fi
    [[ -z ${VISUAL:-} ]] && export VISUAL=$EDITOR
fi
