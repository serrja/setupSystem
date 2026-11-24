-- Startup only uses locally installed, explicitly approved profiles.
local config = vim.fs.dirname(debug.getinfo(1, "S").source:sub(2))
vim.opt.rtp:prepend(config)
vim.g.mapleader = " "
require("config.options")
require("config.keyMaps")

local data = vim.env.XDG_DATA_HOME or (vim.env.HOME .. "/.local/share")
local plugins = data .. "/setup-system/editor/"
local parsers = data .. "/setup-system/editor-parsers"
local modules = data .. "/setup-system/tools/npm/lib/node_modules"
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

local selected = @{ selected_lua }@
local conform = require("conform")
local formatters = {
  python = { python = "ruff_format" }, cpp = { c = "clang_format", cpp = "clang_format" },
  typescript_vue = { javascript = "prettier", javascriptreact = "prettier", typescript = "prettier",
    typescriptreact = "prettier", vue = "prettier" }, dart_flutter = { dart = "dart_format" },
}
local enabled = {}
for _, profile in ipairs(selected) do
  for ft, formatter in pairs(formatters[profile]) do enabled[ft] = {formatter} end
end
local function prettier_root(_, ctx)
  local root = project.root(ctx.buf)
  if not root then return nil end
  for _, name in ipairs({
    ".prettierrc", ".prettierrc.json", ".prettierrc.json5", ".prettierrc.yaml", ".prettierrc.yml",
    ".prettierrc.toml", ".prettierrc.cjs", ".prettierrc.js", ".prettierrc.mjs",
    "prettier.config.js", "prettier.config.cjs", "prettier.config.mjs", "prettier.config.ts",
  }) do
    if vim.fn.filereadable(root .. "/" .. name) == 1 then return root end
  end
  local file = root .. "/package.json"
  if vim.fn.filereadable(file) == 1 then
    local ok, pkg = pcall(vim.json.decode, table.concat(vim.fn.readfile(file), "\n"))
    if ok and type(pkg) == "table" and pkg.prettier ~= nil then return root end
  end
end
conform.setup({ formatters_by_ft = enabled, formatters = {
  prettier = { require_cwd = true, cwd = prettier_root },
} })
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

local function relay(_, messages, context)
  local package = messages[1]
  local id, request, payload = unpack(package)
  local vue = vim.lsp.get_client_by_id(context.client_id)
  local function forward(attempt)
    if not vue then return end
    local ts = vim.lsp.get_clients({bufnr=context.bufnr,name="ts_ls"})[1]
    if not ts and attempt < 10 then return vim.defer_fn(function() forward(attempt+1) end,100) end
    local function reply(_, result)
      local response = {{id,result and result.body or vim.NIL}}
      if vim.fn.has("nvim-0.11") == 1 then vue:notify("tsserver/response",response)
      else vue.notify("tsserver/response",response) end
    end
    if not ts then return reply(nil,nil) end
    local args = {command="typescript.tsserverRequest",arguments={request,payload}}
    if vim.fn.has("nvim-0.11") == 1 then ts:request("workspace/executeCommand",args,reply,context.bufnr)
    else ts.request("workspace/executeCommand",args,reply,context.bufnr) end
  end
  forward(0)
end

for _, profile in ipairs(selected) do
  if profile == "python" then
    start("basedpyright", {"python"}, {"basedpyright-langserver","--stdio"}, {before_init=function(_, spec)
      local python = spec.root_dir .. "/.venv/bin/python"
      if vim.fn.executable(python)==1 then spec.settings={python={pythonPath=python}} end
    end})
  elseif profile == "cpp" then
    start("clangd", {"c","cpp"}, {"clangd"})
  elseif profile == "dart_flutter" then
    start("dartls", {"dart"}, {"dart","language-server","--protocol=lsp"})
  elseif profile == "typescript_vue" then
    start("ts_ls", {"typescript","typescriptreact","javascript","javascriptreact","vue"},
      {"typescript-language-server","--stdio"}, {init_options={
      tsserver={path=modules .. "/typescript/lib/tsserver.js"},
      plugins={{name="@vue/typescript-plugin",location=modules .. "/@vue/typescript-plugin",languages={"vue"}}}}})
    start("vue_ls", {"vue"}, {"vue-language-server","--stdio"},{handlers={["tsserver/request"]=relay}})
  end
end
