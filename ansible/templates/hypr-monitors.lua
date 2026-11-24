[% for monitor in monitors %]hl.monitor({ output = "@{ monitor.output }@", mode = "@{ monitor.mode }@", position = "@{ monitor.x }@x@{ monitor.y }@", scale = @{ monitor.scale }@ })
[% endfor %]