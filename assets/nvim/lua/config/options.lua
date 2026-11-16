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
vim.opt.wrap = false
vim.opt.linebreak = true
vim.opt.breakindent = true
vim.opt.tabstop = 4
vim.opt.shiftwidth = 4
vim.opt.expandtab = true
vim.opt.conceallevel = 0
vim.opt.foldmethod = "manual"
vim.opt.foldenable = false
vim.opt.list = true
vim.opt.listchars = { tab = "→ ", trail = "·", extends = "›", precedes = "‹" }
vim.opt.exrc = false
vim.opt.modeline = false
vim.opt.shadafile = "NONE"
vim.opt.clipboard = ""
vim.opt.statusline = "%f %m %r %= %y %l:%c"
vim.cmd.colorscheme("habamax")

-- Native EditorConfig applies after FileType and remains authoritative.
vim.api.nvim_create_autocmd("FileType", {
  pattern = {
    "lua",
    "javascript",
    "javascriptreact",
    "typescript",
    "typescriptreact",
    "vue",
    "dart",
    "json",
    "yaml",
  },
  callback = function()
    vim.opt_local.shiftwidth = 2
    vim.opt_local.softtabstop = 2
  end,
})

vim.api.nvim_create_autocmd("FileType", {
  pattern = "make",
  callback = function()
    vim.opt_local.expandtab = false
    vim.opt_local.tabstop = 8
    vim.opt_local.shiftwidth = 8
  end,
})

vim.api.nvim_create_autocmd("FileType", {
  pattern = { "markdown", "text" },
  callback = function()
    vim.opt_local.wrap = true
    vim.opt_local.linebreak = true
    vim.opt_local.conceallevel = 0
  end,
})

vim.diagnostic.config({
  virtual_text = false,
  signs = true,
  underline = true,
  severity_sort = true,
  float = { border = "single", source = "if_many" },
})
