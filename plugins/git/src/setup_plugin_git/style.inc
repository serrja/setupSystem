[color]
    ui = auto

[color "branch"]
    current = magenta bold
    remote = cyan

[color "status"]
    added = green bold
    changed = yellow bold
    untracked = red bold

[color "diff"]
    meta = cyan
    frag = magenta bold
    old = red
    new = green

[diff]
    colorMoved = zebra

[alias]
    st = status --short --branch
    graph = log --graph --decorate --oneline --all -20
    last = log -1 --stat
    root = rev-parse --show-toplevel
