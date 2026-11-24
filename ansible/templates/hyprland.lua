require("setup-system.profile")
local extra = (os.getenv("XDG_CONFIG_HOME") or (os.getenv("HOME") .. "/.config")) .. "/hypr/local.lua"
local file=io.open(extra,"r")
if file then file:close(); dofile(extra) end
