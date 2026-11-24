PROJECT_READ := project-list template-versions git-inspect project-editor project-codex
PROJECT_OPERATIONS := project-prepare template-cache template-create template-update git-configure git-use shell-configure rtk-install
.PHONY: $(PROJECT_READ) $(addsuffix -plan,$(PROJECT_OPERATIONS)) $(addsuffix -apply,$(PROJECT_OPERATIONS))
$(PROJECT_READ) $(addsuffix -plan,$(PROJECT_OPERATIONS)):
	@/bin/sh ./libexec/project-task $@
$(addsuffix -apply,$(PROJECT_OPERATIONS)):
	@/bin/sh ./libexec/locked /bin/sh ./libexec/project-task $@
