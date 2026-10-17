# Managed by setupSystem. Sourcing this file never installs software.
[[ -o interactive ]] || return 0
[[ -n ${SETUP_SYSTEM_ZSH_LOADED:-} ]] && return 0
typeset -g SETUP_SYSTEM_ZSH_LOADED=1

source "${0:A:h}/environment.zsh"
source "${0:A:h}/history.zsh"
source "${0:A:h}/prompt.zsh"
source "${0:A:h}/completion.zsh"
source "${0:A:h}/aliases.zsh"
source "${0:A:h}/projects.zsh"
