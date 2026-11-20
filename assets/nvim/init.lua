local source = debug.getinfo(1, "S").source:sub(2)
vim.g.setup_system_config = vim.fs.dirname(vim.fs.normalize(source))
vim.opt.rtp:prepend(vim.g.setup_system_config)
vim.g.mapleader = " "
vim.g.maplocalleader = " "

require("config.options")
require("config.keyMaps")
require("config.pluginManager")
