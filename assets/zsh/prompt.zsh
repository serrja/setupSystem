__setupSystemPrompt() {
    local last_status=$? branch='' dirty='' git_part='' error_part=''
    local dim='' purple='' cyan='' green='' yellow='' red='' reset=''
    if [[ -z ${NO_COLOR:-} && ${TERM:-} != dumb ]]; then
        dim='%F{#6272a4}' purple='%F{#bd93f9}' cyan='%F{#8be9fd}'
        green='%F{#50fa7b}' yellow='%F{#f1fa8c}' red='%F{#ff5555}' reset='%f'
    fi
    if (( $+commands[git] )) && GIT_OPTIONAL_LOCKS=0 git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        branch=$(GIT_OPTIONAL_LOCKS=0 git symbolic-ref --quiet --short HEAD 2>/dev/null) || branch=$(GIT_OPTIONAL_LOCKS=0 git rev-parse --short HEAD 2>/dev/null)
        branch=${branch//\%/%%}
        dirty=$(GIT_OPTIONAL_LOCKS=0 git status --porcelain --untracked-files=no 2>/dev/null)
        git_part=" ${dim}·${reset} ${purple}git:${branch}${reset}"
        [[ -n $dirty ]] && git_part="${git_part} ${yellow}*${reset}"
    fi
    (( last_status != 0 )) && error_part=" ${red}✗ ${last_status}${reset}"
    PROMPT="${dim}╭─${reset} ${purple}%D{%H:%M}${reset} ${cyan}%n@%m${reset} ${dim}·${reset} ${green}%~${reset}${git_part}${error_part}
${dim}╰─${reset} ${green}❯${reset} "
}
autoload -Uz add-zsh-hook
add-zsh-hook precmd __setupSystemPrompt
