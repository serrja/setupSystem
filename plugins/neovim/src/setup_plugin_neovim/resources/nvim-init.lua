-- Startup only uses locally installed, explicitly selected languages.
local config = vim.fs.dirname(debug.getinfo(1, "S").source:sub(2))
vim.opt.rtp:prepend(config)
vim.g.mapleader = " "
require("config.options")
require("config.keyMaps")

local data = vim.env.XDG_DATA_HOME or (vim.env.HOME .. "/.local/share")
local plugins = data .. "/setup-system/editor/"
local parsers = data .. "/setup-system/editor-parsers"
if not vim.uv.fs_stat(plugins .. "lazy") then return end
vim.opt.rtp:prepend(plugins .. "lazy")
local specs = {}
for _, name in ipairs(@{ plugin_lua }@) do
  if name ~= "lazy" then
    table.insert(specs, { name = name, dir = plugins .. name, lazy = false })
  end
end
require("lazy").setup(specs, { install = { missing = false }, checker = { enabled = false },
  change_detection = { enabled = false }, rocks = { enabled = false }, local_spec = false,
  performance = { rtp = { reset = false } }, lockfile = vim.fn.stdpath("state") .. "/lazy-lock.json" })

local completion = require("blink.cmp")
completion.setup({ fuzzy = { implementation = "lua", prebuilt_binaries = { download = false } },
  keymap = { preset = "default" }, sources = { default = { "lsp", "path", "buffer" } } })
require("oil").setup({ default_file_explorer = false, columns = {}, view_options = { show_hidden = false } })
require("gitsigns").setup({ current_line_blame = false })
vim.opt.rtp:prepend(parsers)
require("nvim-treesitter.configs").setup({ parser_install_dir = parsers, auto_install = false })
vim.api.nvim_create_autocmd("FileType",{callback=function(ev) pcall(vim.treesitter.start,ev.buf) end})
local project = require("config.project")
local fzf = require("fzf-lua")
local excluded = @{ editor_catalog.search | to_json | editor_globs_lua }@
local rgFlags = table.concat(
  vim.tbl_map(function(glob)
    return "-g " .. vim.fn.shellescape("!" .. glob)
  end, excluded),
  " "
)

fzf.setup({
  files = { cmd = "rg --files " .. rgFlags, hidden = false, follow = false },
  grep = { rg_opts = "--column --line-number --no-heading --color=always --smart-case " .. rgFlags },
})
for key, method in pairs({ ["<leader>ff"]="files", ["<leader>fg"]="live_grep", ["<leader>gs"]="git_status", ["<leader>fs"]="lsp_document_symbols", ["<leader>fS"]="lsp_live_workspace_symbols" }) do
  vim.keymap.set("n",key,function()
    local root = project.requireRoot()
    if root then fzf[method]({cwd=root}) end
  end, {desc=method})
end
vim.keymap.set("n","-",function() require("oil").open() end,{desc="Project files"})

local selected = @{ render_ctx.selected_lua }@
local conform = require("conform")
local enabled = {}
if vim.tbl_contains(selected, "python") then enabled.python = {"ruff_format"} end
conform.setup({formatters_by_ft = enabled})
vim.api.nvim_create_user_command("Format", function()
  if project.requireRoot() then conform.format({async=false,timeout_ms=15000,lsp_format="never"}) end
end, {})

local function start(name, types, cmd, options)
  vim.api.nvim_create_autocmd("FileType", {pattern=types,callback=function(ev)
    local root = project.root(ev.buf)
    if root then
      local spec = vim.tbl_extend("force", options or {}, { name=name, cmd=cmd, root_dir=root,
        capabilities=completion.get_lsp_capabilities() })
      vim.lsp.start(spec, {bufnr=ev.buf})
    end
  end})
end

if vim.tbl_contains(selected, "python") then
  start("basedpyright", {"python"}, {"basedpyright-langserver","--stdio"}, {before_init=function(_, spec)
    local python = spec.root_dir .. "/.venv/bin/python"
    if vim.fn.executable(python)==1 then spec.settings={python={pythonPath=python}} end
  end})
end
