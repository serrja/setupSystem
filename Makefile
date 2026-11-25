# Runtime recipes use only packaged includes. The caller selects one fixed target.
.DEFAULT_GOAL := help
.NOTPARALLEL:
override SHELL := /bin/sh
# Generate the assignment from fixed names. $(value ...) copies the raw data at parse time.
override runtime_environment := HOME XDG_CONFIG_HOME XDG_DATA_HOME XDG_STATE_HOME XDG_CACHE_HOME \
    XDG_RUNTIME_DIR WAYLAND_DISPLAY HYPRLAND_INSTANCE_SIGNATURE CODEX_HOME DISPLAY \
    SETUP_SYSTEM_REQUEST SETUP_SYSTEM_RESULT SETUP_SYSTEM_CANCEL SETUP_SYSTEM_INTERPRETER
override define export_literal
ifneq ($$(origin $(1)),undefined)
override export $(1) := $$(value $(1))
endif
endef
$(foreach name,$(runtime_environment),$(eval $(call export_literal,$(name))))
override PLAY := /bin/sh ./libexec/play.sh
.PHONY: help run prepare
help:
	@printf '%s\n' 'make prepare  | prepare a development environment' \
	  'make package  | build the Debian package' 'make install  | install the prepared package' \
	  'make update   | update the installed package' 'make run      | open the configurator'
run:
	@/bin/sh ./libexec/run.sh
prepare:
	uv sync --locked --all-groups
include mk/files.mk mk/projects.mk mk/base.mk mk/admin.mk mk/workspace.mk mk/package.mk mk/quality.mk
