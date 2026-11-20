local module = {}

local function projectRoot(bufferId, onDir)
  local root = require("config.project").root(bufferId)
  if root then
    onDir(root)
  end
end

function module.setup()
  local capabilities = require("blink.cmp").get_lsp_capabilities()
  local settings = vim.json.decode(table.concat(vim.fn.readfile(vim.g.setup_system_config .. "/languages.json")))
  local configs = {}
  local data = vim.env.XDG_DATA_HOME or (vim.env.HOME .. "/.local/share")
  local nodeModules = data .. "/setup-system/language-tools/typescript-vue/" .. settings.node_environment .. "/node_modules"
  for _, profile in ipairs(settings.selected) do
    if profile == "python" then
      table.insert(configs, { name = "basedpyright", command = { "basedpyright-langserver", "--stdio" } })
    elseif profile == "cpp" then
      table.insert(configs, { name = "clangd", command = { "clangd" } })
    elseif profile == "dart_flutter" then
      table.insert(configs, { name = "dartls", command = { "dart", "language-server", "--protocol=lsp" } })
    elseif profile == "typescript_vue" then
      table.insert(configs, { name = "vue_ls", command = { "vue-language-server", "--stdio" } })
      table.insert(configs, { name = "ts_ls", command = { "typescript-language-server", "--stdio" } })
    end
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
      elseif item.name == "ts_ls" then
        config.filetypes =
          { "javascript", "javascriptreact", "typescript", "typescriptreact", "vue" }
        config.init_options = {
          tsserver = { path = nodeModules .. "/typescript/lib/tsserver.js" },
          plugins = {
            { name = "@vue/typescript-plugin", location = nodeModules .. "/@vue/typescript-plugin", languages = { "vue" } },
          },
        }
      end
      vim.lsp.config(item.name, config)
      vim.lsp.enable(item.name)
    end
  end
end

return module
