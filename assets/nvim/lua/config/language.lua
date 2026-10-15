local module = {}

local function projectRoot(bufferId, onDir)
  local root = vim.uv.fs_realpath(vim.fn.getcwd())
  local filePath = vim.uv.fs_realpath(vim.api.nvim_buf_get_name(bufferId))
  if root and filePath and filePath:sub(1, #root + 1) == root .. "/" then
    onDir(root)
  end
end

function module.setup()
  local capabilities = require("blink.cmp").get_lsp_capabilities()
  local projectPython = vim.fn.getcwd() .. "/.venv/bin/python"
  local pythonSettings = {
    basedpyright = {
      disableOrganizeImports = true,
      analysis = { diagnosticMode = "openFilesOnly" },
    },
  }
  if vim.fn.executable(projectPython) == 1 then
    pythonSettings.python = { pythonPath = projectPython }
  end
  local configs = {
    { name = "basedpyright", command = "basedpyright-langserver" },
    { name = "clangd", command = "clangd" },
    { name = "lua_ls", command = "lua-language-server" },
  }
  for _, item in ipairs(configs) do
    if vim.fn.executable(item.command) == 1 then
      local settings = item.name == "basedpyright" and pythonSettings or nil
      vim.lsp.config(
        item.name,
        { capabilities = capabilities, root_dir = projectRoot, settings = settings }
      )
      vim.lsp.enable(item.name)
    end
  end
end

return module
