vim.opt.number = true
vim.opt.relativenumber = true
vim.opt.signcolumn = "yes"
vim.opt.termguicolors = true
vim.opt.splitbelow = true
vim.opt.splitright = true
vim.opt.ignorecase = true
vim.opt.smartcase = true
vim.opt.undofile = true
vim.opt.updatetime = 300
vim.opt.exrc = false
vim.opt.modeline = false
vim.opt.shadafile = "NONE"
vim.opt.clipboard = ""
vim.opt.statusline = "%f %m %r %= %y %l:%c"
vim.cmd.colorscheme("habamax")

vim.diagnostic.config({
  virtual_text = false,
  signs = true,
  underline = true,
  severity_sort = true,
  float = { border = "single", source = "if_many" },
})
