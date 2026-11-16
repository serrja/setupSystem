local map = vim.keymap.set

local excluded = {
  "!**/.env*",
  "!**/.aws/**",
  "!**/.git/**",
  "!**/.cache/**",
  "!**/.venv/**",
  "!**/node_modules/**",
  "!**/credentials.json",
  "!**/credentials.toml",
  "!**/secrets.*",
  "!**/*.pem",
  "!**/*.key",
  "!**/*.p12",
  "!**/*.pfx",
  "!**/id_rsa*",
  "!**/id_ed25519*",
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

local function picker(name)
  return function()
    local root = require("config.project").requireRoot()
    if root then
      require("fzf-lua")[name]({ cwd = root })
    end
  end
end

return {
  {
    "ibhagwan/fzf-lua",
    cmd = "FzfLua",
    keys = {
      { "<leader>ff", picker("files"), desc = "Find project files" },
      { "<leader>fg", picker("live_grep"), desc = "Search project text" },
      { "<leader>fb", "<cmd>FzfLua buffers<cr>", desc = "Find buffers" },
      { "<leader>fs", "<cmd>FzfLua lsp_document_symbols<cr>", desc = "Document symbols" },
      { "<leader>fS", picker("lsp_live_workspace_symbols"), desc = "Workspace symbols" },
      { "<leader>gs", picker("git_status"), desc = "Git status" },
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
    dependencies = { "nvim-tree/nvim-web-devicons" },
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
    "nvim-tree/nvim-web-devicons",
    lazy = true,
    opts = { default = true },
  },
  {
    "stevearc/aerial.nvim",
    cmd = { "AerialToggle", "AerialOpen", "AerialNavToggle" },
    keys = {
      { "<leader>o", "<cmd>AerialToggle<cr>", desc = "File outline" },
    },
    dependencies = { "nvim-tree/nvim-web-devicons" },
    opts = {
      backends = { "lsp", "treesitter", "markdown" },
      layout = { default_direction = "right" },
      attach_mode = "window",
    },
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
      map("n", "<leader>gD", signs.diffthis, { desc = "Git diff current file" })
    end,
  },
  {
    "folke/which-key.nvim",
    event = "VeryLazy",
    keys = {
      {
        "<leader>?",
        function()
          require("which-key").show({ global = true })
        end,
        desc = "Keymap help",
      },
    },
    opts = { preset = "classic", icons = { mappings = false } },
  },
}
