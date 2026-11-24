hl.config({ input = {
  kb_layout = "@{ keyboard }@",
[% if ',' in keyboard %]  kb_options = "grp:win_space_toggle",
[% endif %]} })
