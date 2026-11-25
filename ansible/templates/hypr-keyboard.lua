hl.config({ input = {
  kb_layout = "@{ render_ctx.keyboard }@",
[% if ',' in render_ctx.keyboard %]  kb_options = "grp:win_space_toggle",
[% endif %]} })
