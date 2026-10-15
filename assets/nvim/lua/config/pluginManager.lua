local managerPath = vim.fn.stdpath("data") .. "/lazy/lazy.nvim"
if vim.fn.isdirectory(managerPath) == 0 then
  vim.notify(
    "Plugins are unavailable. Run: setup-system apply --profile terminalDev --yes",
    vim.log.levels.WARN
  )
  return
end

vim.opt.rtp:prepend(managerPath)
require("lazy").setup({ { import = "plugins" } }, {
  local_spec = false,
  install = { missing = false },
  checker = { enabled = false },
  change_detection = { enabled = false },
  pkg = { enabled = false },
  rocks = { enabled = false },
  lockfile = vim.fn.stdpath("config") .. "/lazy-lock.json",
})
