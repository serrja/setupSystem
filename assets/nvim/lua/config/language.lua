local module = {}

local function projectRoot(bufferId, onDir)
  local root = require("config.project").root(bufferId)
  if root then
    onDir(root)
  end
end

function module.setup()
  local capabilities = require("blink.cmp").get_lsp_capabilities()
  local configs = {
    { name = "basedpyright", command = { "basedpyright-langserver", "--stdio" } },
    { name = "clangd", command = { "clangd" } },
    { name = "lua_ls", command = { "lua-language-server" } },
    { name = "ts_ls", command = { "typescript-language-server", "--stdio" } },
    { name = "dartls", command = { "dart", "language-server", "--protocol=lsp" } },
    { name = "jsonls", command = { "vscode-json-language-server", "--stdio" } },
    { name = "yamlls", command = { "yaml-language-server", "--stdio" } },
    { name = "marksman", command = { "marksman", "server" } },
  }
  local vuePlugin = vim.fn.stdpath("data") .. "/tools/node_modules/@vue/typescript-plugin"
  local vueReady = vim.fn.executable("vue-language-server") == 1
    and vim.fn.executable("typescript-language-server") == 1
    and vim.fn.isdirectory(vuePlugin) == 1
  if vueReady then
    table.insert(configs, { name = "vue_ls", command = { "vue-language-server", "--stdio" } })
  end
  for _, item in ipairs(configs) do
    if vim.fn.executable(item.command[1]) == 1 then
      local config = { capabilities = capabilities, root_dir = projectRoot, cmd = item.command }
      if item.name == "basedpyright" then
        config.settings = {
          basedpyright = {
            disableOrganizeImports = true,
            analysis = { diagnosticMode = "openFilesOnly" },
          },
        }
        config.before_init = function(_, clientConfig)
          local python = clientConfig.root_dir .. "/.venv/bin/python"
          if vim.fn.executable(python) == 1 then
            clientConfig.settings.python = { pythonPath = python }
          end
        end
      elseif item.name == "lua_ls" then
        config.settings = {
          Lua = {
            runtime = { version = "LuaJIT" },
            diagnostics = { globals = { "vim" } },
            workspace = { checkThirdParty = false },
            telemetry = { enable = false },
          },
        }
      elseif item.name == "ts_ls" and vueReady then
        config.filetypes =
          { "javascript", "javascriptreact", "typescript", "typescriptreact", "vue" }
        config.init_options = {
          plugins = {
            { name = "@vue/typescript-plugin", location = vuePlugin, languages = { "vue" } },
          },
        }
      end
      vim.lsp.config(item.name, config)
      vim.lsp.enable(item.name)
    end
  end
end

return module
