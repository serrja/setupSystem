-- Called by the confirmed editor operation in an empty, temporary project.
assert(vim.v.errmsg == "", vim.v.errmsg)
assert(require("lazy").plugins()[1], "No managed plugins loaded")
assert(require("blink.cmp").get_lsp_capabilities(), "Completion unavailable")
assert(require("oil").open and require("fzf-lua").files and require("gitsigns").stage_hunk)
local conform = require("conform")
for _, tool in ipairs({ "rg", "fzf", "git", "wl-copy", "wl-paste" }) do
  assert(vim.fn.executable(tool) == 1, tool .. " unavailable")
end
if vim.env.WAYLAND_DISPLAY then
  assert(vim.o.clipboard == "unnamedplus" and vim.fn.has("clipboard") == 1, "Wayland clipboard unavailable")
end
local parsers = vim.json.decode(vim.env.SETUP_EDITOR_PARSERS)
local missing = {}
for _, language in ipairs(parsers) do
  local ok, parser = pcall(vim.treesitter.get_string_parser, "", language)
  if not ok or not parser then
    table.insert(missing, language)
  end
end
if #missing > 0 and vim.env.SETUP_EDITOR_INSTALL == "1" then
  require("nvim-treesitter").install(missing):wait(300000)
end
for _, language in ipairs(parsers) do
  local trees = vim.treesitter.get_string_parser("", language):parse()
  assert(trees and trees[1], "Could not parse " .. language)
end
local checks = {
  python = { { "check.py", "basedpyright", "ruff_format" } },
  cpp = { { "check.cpp", "clangd", "clang_format" } },
  dart_flutter = { { "check.dart", "dartls", "dart_format" } },
  typescript_vue = { { "check.ts", "ts_ls", "prettier" }, { "check.vue", "vue_ls", "prettier" } },
}
for _, profile in ipairs(vim.json.decode(vim.env.SETUP_EDITOR_PROFILES)) do
  for _, check in ipairs(checks[profile]) do
    local path = vim.fn.getcwd() .. "/" .. check[1]
    vim.fn.writefile({}, path)
    vim.cmd.edit(vim.fn.fnameescape(path))
    local buf = vim.api.nvim_get_current_buf()
    assert(vim.wait(30000, function()
      local client = vim.lsp.get_clients({ name = check[2], bufnr = buf })[1]
      return client ~= nil and client.initialized
    end, 50), "Language server failed to initialize: " .. check[2])
    assert(conform.get_formatter_info(check[3], buf).available, "Formatter unavailable: " .. check[3])
    local formatted, formatError = false, nil
    conform.format({ bufnr = buf, formatters = { check[3] }, async = false, timeout_ms = 15000 },
      function(err)
        formatted = true
        formatError = err
      end)
    assert(formatted and not formatError, "Formatter failed: " .. check[3])

  end
end
io.write("setup-system editor verified\n")
