__setupSystemPrompt() {
    local last_status=$? branch='' dirty='' git_part='' error_part=''
    local dim='' mauve='' teal='' green='' yellow='' red='' reset=''
    if [[ -z ${NO_COLOR:-} && ${TERM:-} != dumb ]]; then
        dim='%F{@{ subtext0 }@}' mauve='%F{@{ mauve }@}' teal='%F{@{ teal }@}'
        green='%F{@{ green }@}' yellow='%F{@{ yellow }@}' red='%F{@{ red }@}' reset='%f'
    fi
    if (( $+commands[git] )) && GIT_OPTIONAL_LOCKS=0 git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        branch=$(GIT_OPTIONAL_LOCKS=0 git symbolic-ref --quiet --short HEAD 2>/dev/null) || branch=$(GIT_OPTIONAL_LOCKS=0 git rev-parse --short HEAD 2>/dev/null)
        branch=${branch//\%/%%}
        dirty=$(GIT_OPTIONAL_LOCKS=0 git status --porcelain --untracked-files=no 2>/dev/null)
        git_part=" ${dim}·${reset} ${mauve}git:${branch}${reset}"
        [[ -n $dirty ]] && git_part="${git_part} ${yellow}*${reset}"
    fi
    (( last_status != 0 )) && error_part=" ${red}✗ ${last_status}${reset}"
    PROMPT="${dim}╭─${reset} ${mauve}%D{%H:%M}${reset} ${teal}%n@%m${reset} ${dim}·${reset} ${green}%~${reset}${git_part}${error_part}
${dim}╰─${reset} ${green}❯${reset} "
}
autoload -Uz add-zsh-hook
add-zsh-hook precmd __setupSystemPrompt
