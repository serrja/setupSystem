local module = {}

local markers =
  { "pyproject.toml", "package.json", "pubspec.yaml", "CMakeLists.txt", ".luarc.json" }

local function inside(path, root)
  return path == root or path:sub(1, #root + 1) == root .. "/"
end

local function findRoot(directory)
  local home = vim.uv.fs_realpath(vim.env.HOME or "")
  while directory and directory ~= "/" and directory ~= home do
    local git = directory .. "/.git"
    local stat = vim.uv.fs_stat(git)
    -- Empty grouping .git directories are not repositories.
    if stat and (stat.type == "file" or vim.uv.fs_stat(git .. "/HEAD")) then
      return directory
    end
    for _, marker in ipairs(markers) do
      if vim.uv.fs_stat(directory .. "/" .. marker) then
        return directory
      end
    end
    directory = vim.fs.dirname(directory)
  end
end

function module.root(bufferId)
  local cwd = vim.uv.fs_realpath(vim.fn.getcwd())
  local boundary = cwd and findRoot(cwd)
  if not boundary then
    return nil
  end
  local name = vim.api.nvim_buf_get_name(bufferId or 0)
  if name == "" then
    return boundary
  end
  local path = vim.uv.fs_realpath(name)
  if not path or not inside(path, boundary) then
    return nil
  end
  local directory = vim.fn.isdirectory(path) == 1 and path or vim.fs.dirname(path)
  local root = findRoot(directory)
  return root and inside(root, boundary) and root or nil
end

function module.requireRoot()
  local root = module.root(0)
  if not root then
    vim.notify("Open Neovim from the intended repository root", vim.log.levels.WARN)
  end
  return root
end

return module
