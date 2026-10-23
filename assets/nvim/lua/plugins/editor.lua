return {
  {
    "saghen/blink.cmp",
    lazy = false,
    opts = {
      fuzzy = { implementation = "lua" },
      keymap = { preset = "default" },
      appearance = { nerd_font_variant = "normal" },
      completion = { documentation = { auto_show = false } },
      sources = { default = { "lsp", "path", "buffer" } },
    },
  },
  {
    "neovim/nvim-lspconfig",
    lazy = false,
    dependencies = { "saghen/blink.cmp" },
    config = function()
      require("config.language").setup()
    end,
  },
  {
    "stevearc/conform.nvim",
    lazy = false,
    opts = {
      formatters_by_ft = {
        python = { "ruff_format" },
        lua = { "stylua" },
        c = { "clang_format" },
        cpp = { "clang_format" },
        javascript = { "prettier" },
        javascriptreact = { "prettier" },
        typescript = { "prettier" },
        typescriptreact = { "prettier" },
        vue = { "prettier" },
        dart = { "dart_format" },
      },
      formatters = {
        prettier = {
          require_cwd = true,
          cwd = function(_, context)
            local root = require("config.project").root(context.buf)
            if not root then
              return nil
            end
            for _, name in ipairs({
              ".prettierrc",
              ".prettierrc.json",
              ".prettierrc.yaml",
              ".prettierrc.yml",
              ".prettierrc.cjs",
              "prettier.config.js",
              "prettier.config.mjs",
              "prettier.config.cjs",
            }) do
              if vim.fn.filereadable(root .. "/" .. name) == 1 then
                return root
              end
            end
          end,
        },
      },
      format_on_save = nil,
    },
  },
  {
    "nvim-treesitter/nvim-treesitter",
    lazy = false,
    config = function()
      vim.api.nvim_create_autocmd("FileType", {
        pattern = {
          "python",
          "lua",
          "c",
          "cpp",
          "json",
          "toml",
          "yaml",
          "markdown",
          "bash",
          "javascript",
          "javascriptreact",
          "typescript",
          "typescriptreact",
          "vue",
          "dart",
        },
        callback = function(event)
          local ok = pcall(vim.treesitter.start, event.buf)
          if not ok then
            vim.notify_once(
              "Tree-sitter parser missing; run terminalDev doctor",
              vim.log.levels.WARN
            )
          end
        end,
      })
    end,
  },
}
