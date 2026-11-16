local map = vim.keymap.set

map("n", "<leader>w", "<cmd>write<cr>", { desc = "Save buffer" })
map("n", "<leader>q", "<cmd>quit<cr>", { desc = "Quit window" })
map("n", "<leader>sv", "<cmd>vsplit<cr>", { desc = "Vertical split" })
map("n", "<leader>sh", "<cmd>split<cr>", { desc = "Horizontal split" })
map("n", "<leader>tt", "<cmd>split | terminal<cr>", { desc = "Terminal split" })
map("t", "<Esc><Esc>", "<C-\\><C-n>", { desc = "Terminal normal mode" })
map("n", "[d", vim.diagnostic.goto_prev, { desc = "Previous diagnostic" })
map("n", "]d", vim.diagnostic.goto_next, { desc = "Next diagnostic" })
map("n", "<leader>dd", vim.diagnostic.open_float, { desc = "Diagnostic details" })
map("n", "gd", vim.lsp.buf.definition, { desc = "Definition" })
map("n", "gr", vim.lsp.buf.references, { desc = "References" })
map("n", "<leader>rn", vim.lsp.buf.rename, { desc = "Rename symbol" })
map("n", "<leader>ca", vim.lsp.buf.code_action, { desc = "Code action" })
map("n", "gI", vim.lsp.buf.implementation, { desc = "Implementation" })
map("n", "<leader>ci", vim.lsp.buf.incoming_calls, { desc = "Incoming calls (LSP)" })
map("n", "<leader>co", vim.lsp.buf.outgoing_calls, { desc = "Outgoing calls (LSP)" })
map("n", "[b", "<cmd>bprevious<cr>", { desc = "Previous buffer" })
map("n", "]b", "<cmd>bnext<cr>", { desc = "Next buffer" })
map("n", "<leader>uw", function()
  vim.opt_local.wrap = not vim.opt_local.wrap:get()
end, { desc = "Toggle soft wrap" })

map("n", "<leader>cf", function()
  local filePath = vim.api.nvim_buf_get_name(0)
  if filePath:find("/Obsidian Vault/", 1, true) then
    vim.notify("Formatting is disabled for the vault", vim.log.levels.WARN)
    return
  end
  if not require("config.project").requireRoot() then
    return
  end
  require("conform").format({ async = false, timeout_ms = 5000, lsp_format = "never" })
end, { desc = "Format explicitly" })
