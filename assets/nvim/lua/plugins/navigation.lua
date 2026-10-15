local map = vim.keymap.set

local excluded = {
  "!**/.env*",
  "!**/*auth*",
  "!**/backups/**",
  "!**/.ssh/**",
  "!**/.gnupg/**",
  "!**/.local/state/**",
}
local rgFlags = table.concat(
  vim.tbl_map(function(item)
    return "-g " .. vim.fn.shellescape(item)
  end, excluded),
  " "
)

return {
  {
    "ibhagwan/fzf-lua",
    cmd = "FzfLua",
    keys = {
      { "<leader>ff", "<cmd>FzfLua files<cr>", desc = "Find files" },
      { "<leader>fg", "<cmd>FzfLua live_grep<cr>", desc = "Search text" },
      { "<leader>fb", "<cmd>FzfLua buffers<cr>", desc = "Find buffers" },
      { "<leader>fs", "<cmd>FzfLua lsp_document_symbols<cr>", desc = "Document symbols" },
      { "<leader>gs", "<cmd>FzfLua git_status<cr>", desc = "Git status" },
    },
    config = function()
      local fzf = require("fzf-lua")
      fzf.setup({
        files = { cmd = "rg --files " .. rgFlags, hidden = false, follow = false },
        grep = {
          rg_opts = "--column --line-number --no-heading --color=always --smart-case " .. rgFlags,
        },
      })
    end,
  },
  {
    "stevearc/oil.nvim",
    lazy = false,
    opts = {
      default_file_explorer = false,
      columns = { "icon" },
      view_options = { show_hidden = false },
    },
    config = function(_, opts)
      require("oil").setup(opts)
      map("n", "<leader>e", "<cmd>Oil<cr>", { desc = "File explorer" })
    end,
  },
  {
    "lewis6991/gitsigns.nvim",
    lazy = false,
    opts = { signcolumn = true, current_line_blame = false },
    config = function(_, opts)
      local signs = require("gitsigns")
      signs.setup(opts)
      map("n", "<leader>gh", signs.preview_hunk, { desc = "Preview Git hunk" })
      map("n", "<leader>gb", signs.blame_line, { desc = "Blame line" })
    end,
  },
  {
    "folke/which-key.nvim",
    event = "VeryLazy",
    opts = { preset = "classic", icons = { mappings = false } },
  },
}
