return {
  {
    "catppuccin/nvim",
    name = "catppuccin",
    priority = 1000,
    lazy = false,
    opts = {
      flavour = "mocha",
      color_overrides = {
        mocha = {
          base = "{{ base }}",
          mantle = "{{ mantle }}",
          crust = "{{ crust }}",
          surface0 = "{{ surface0 }}",
          text = "{{ text }}",
          subtext0 = "{{ subtext0 }}",
          green = "{{ green }}",
          teal = "{{ teal }}",
          red = "{{ red }}",
          yellow = "{{ yellow }}",
        },
      },
      custom_highlights = function(colors)
        return { StatusLine = { fg = colors.green, bg = colors.surface0 } }
      end,
    },
    config = function(_, options)
      require("catppuccin").setup(options)
      vim.cmd.colorscheme("catppuccin-mocha")
    end,
  },
}
