vim.cmd("highlight clear")
vim.g.colors_name = "catppuccin_mocha_green"
local colors = {
  base = "@{ base }@",
  text = "@{ text }@",
  green = "@{ green }@",
  surface = "@{ surface0 }@",
  muted = "@{ subtext0 }@",
  red = "@{ red }@",
  yellow = "@{ yellow }@",
  teal = "@{ teal }@",
}
local groups = {
  Normal = { fg = colors.text, bg = colors.base },
  NormalFloat = { fg = colors.text, bg = colors.surface },
  Comment = { fg = colors.muted, italic = true },
  String = { fg = colors.green },
  Function = { fg = colors.teal },
  Keyword = { fg = colors.green, bold = true },
  Visual = { bg = colors.surface },
  Search = { fg = colors.base, bg = colors.yellow },
  DiagnosticError = { fg = colors.red },
  DiagnosticWarn = { fg = colors.yellow },
  LineNr = { fg = colors.muted },
  CursorLineNr = { fg = colors.green, bold = true },
  StatusLine = { fg = colors.green, bg = colors.surface },
}
for name, attributes in pairs(groups) do
  vim.api.nvim_set_hl(0, name, attributes)
end
