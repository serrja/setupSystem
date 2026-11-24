-- setupSystem profile for Hyprland; additions belong after the require.
local cfg = (os.getenv("XDG_CONFIG_HOME") or (os.getenv("HOME") .. "/.config"))
  .. "/hypr/setup-system"
local function quote(value)
  return "'" .. value:gsub("'", "'\"'\"'") .. "'"
end
local function exec(command)
  return hl.dsp.exec_cmd(command)
end
local function bind(key, action, description)
  hl.bind("SUPER + " .. key, action, { description = description })
end
hl.env("XCURSOR_THEME", "Adwaita")
hl.env("XCURSOR_SIZE", "24")
hl.env("QT_QPA_PLATFORMTHEME", "gtk3")
hl.monitor({ output = "", mode = "preferred", position = "auto", scale = "auto" })
for _, extra in ipairs({"monitors.lua", "keyboard.lua"}) do
  local path = cfg .. "/" .. extra
  local file = io.open(path, "r")
  if file then file:close(); dofile(path) end
end
hl.config({
  general = {
    gaps_in = 6,
    gaps_out = 12,
    border_size = 2,
    layout = "dwindle",
    col = { active_border = "rgb(@{ green_hex }@)", inactive_border = "rgb(@{ surface1_hex }@)" },
  },
  decoration = {
    rounding = @{ rounding }@,
    blur = { enabled = @{ effects }@, size = 3, passes = 1 },
    shadow = { enabled = @{ effects }@, range = 4, render_power = 3, color = "rgba(@{ crust_hex }@99)" },
  },
  animations = { enabled = @{ effects }@ },
  input = {
    follow_mouse = 1,
  },
  misc = { disable_hyprland_logo = true, force_default_wallpaper = 0 },
})
hl.on("hyprland.start", function()
  hl.exec_cmd("sh " .. quote(cfg .. "/session.sh"))
end)
bind("Return", exec("foot -c " .. quote(cfg .. "/foot.ini")), "Terminal")
bind(
  "F1",
  exec(
    "wofi --show drun --conf "
      .. quote(cfg .. "/wofi.conf")
      .. " --style "
      .. quote(cfg .. "/style.css")
  ),
  "Applications"
)
bind(
  "D",
  exec(
    "wofi --show drun --conf "
      .. quote(cfg .. "/wofi.conf")
      .. " --style "
      .. quote(cfg .. "/style.css")
  ),
  "Applications"
)
bind("E", exec("thunar"), "Files")
bind("F2", exec("pavucontrol"), "Audio settings")
bind("F3", exec("nm-connection-editor"), "Network settings")
bind("F4", hl.dsp.window.close(), "Close window")
bind("F5", hl.dsp.window.float({ action = "toggle" }), "Floating")
bind("F11", hl.dsp.window.fullscreen(), "Fullscreen")
bind("Escape", exec("loginctl lock-session"), "Lock")
bind("Print", exec("sh " .. quote(cfg .. "/screenshot.sh")), "Screenshot area to clipboard")
bind(
  "F8",
  exec(
    "cliphist list | wofi --dmenu --style "
      .. quote(cfg .. "/style.css")
      .. " | cliphist decode | wl-copy"
  ),
  "Clipboard history"
)
bind("F9", exec("sh " .. quote(cfg .. "/power.sh")), "Power menu")
bind(
  "F12",
  exec("foot -c " .. quote(cfg .. "/foot.ini") .. " -e less " .. quote(cfg .. "/keys.txt")),
  "Help"
)
for _, direction in ipairs({ "left", "right", "up", "down" }) do
  bind(direction, hl.dsp.focus({ direction = direction }), "Focus " .. direction)
  bind("SHIFT + " .. direction, hl.dsp.window.move({ direction = direction }), "Move " .. direction)
  local x = direction == "left" and -20 or (direction == "right" and 20 or 0)
  local y = direction == "up" and -20 or (direction == "down" and 20 or 0)
  bind(
    "CTRL + " .. direction,
    hl.dsp.window.resize({ x = x, y = y, relative = true }),
    "Resize " .. direction
  )
end
for i = 1, 10 do
  bind(tostring(i % 10), hl.dsp.focus({ workspace = i }), "Workspace " .. i)
  bind("SHIFT + " .. (i % 10), hl.dsp.window.move({ workspace = i }), "Move to workspace " .. i)
end
hl.bind("SUPER + mouse:272", hl.dsp.window.drag(), { mouse = true, description = "Move" })
hl.bind("SUPER + mouse:273", hl.dsp.window.resize(), { mouse = true, description = "Resize" })
for key, command in pairs({
  XF86AudioNext = "playerctl next",
  XF86AudioPrev = "playerctl previous",
  XF86AudioPlay = "playerctl play-pause",
  XF86AudioPause = "playerctl play-pause",
  XF86AudioRaiseVolume = "wpctl set-volume -l 1 @DEFAULT_AUDIO_SINK@ 5%+",
  XF86AudioLowerVolume = "wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-",
  XF86AudioMute = "wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle",
  XF86AudioMicMute = "wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle",
  XF86MonBrightnessUp = "sh " .. quote(cfg .. "/brightness.sh") .. " up",
  XF86MonBrightnessDown = "sh " .. quote(cfg .. "/brightness.sh") .. " down",
}) do
  hl.bind(key, exec(command), { repeating = true, description = key })
end
