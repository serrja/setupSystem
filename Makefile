# Runtime recipes use only packaged includes. The caller selects one fixed target.
.DEFAULT_GOAL := help
.NOTPARALLEL:
override SHELL := /bin/sh
# Preserve literal environment values across the Make-to-recipe boundary.
ifneq ($(origin HOME),undefined)
override export HOME := $(value HOME)
endif
ifneq ($(origin XDG_CONFIG_HOME),undefined)
override export XDG_CONFIG_HOME := $(value XDG_CONFIG_HOME)
endif
ifneq ($(origin XDG_DATA_HOME),undefined)
override export XDG_DATA_HOME := $(value XDG_DATA_HOME)
endif
ifneq ($(origin XDG_STATE_HOME),undefined)
override export XDG_STATE_HOME := $(value XDG_STATE_HOME)
endif
ifneq ($(origin XDG_CACHE_HOME),undefined)
override export XDG_CACHE_HOME := $(value XDG_CACHE_HOME)
endif
ifneq ($(origin XDG_RUNTIME_DIR),undefined)
override export XDG_RUNTIME_DIR := $(value XDG_RUNTIME_DIR)
endif
ifneq ($(origin WAYLAND_DISPLAY),undefined)
override export WAYLAND_DISPLAY := $(value WAYLAND_DISPLAY)
endif
ifneq ($(origin HYPRLAND_INSTANCE_SIGNATURE),undefined)
override export HYPRLAND_INSTANCE_SIGNATURE := $(value HYPRLAND_INSTANCE_SIGNATURE)
endif
ifneq ($(origin CODEX_HOME),undefined)
override export CODEX_HOME := $(value CODEX_HOME)
endif
ifneq ($(origin DISPLAY),undefined)
override export DISPLAY := $(value DISPLAY)
endif
ifneq ($(origin SETUP_SYSTEM_REQUEST),undefined)
override export SETUP_SYSTEM_REQUEST := $(value SETUP_SYSTEM_REQUEST)
endif
ifneq ($(origin SETUP_SYSTEM_RESULT),undefined)
override export SETUP_SYSTEM_RESULT := $(value SETUP_SYSTEM_RESULT)
endif
ifneq ($(origin SETUP_SYSTEM_CANCEL),undefined)
override export SETUP_SYSTEM_CANCEL := $(value SETUP_SYSTEM_CANCEL)
endif
ifneq ($(origin SETUP_SYSTEM_INTERPRETER),undefined)
override export SETUP_SYSTEM_INTERPRETER := $(value SETUP_SYSTEM_INTERPRETER)
endif
override PLAY := /bin/sh ./libexec/play
.PHONY: help run prepare
help:
	@printf '%s\n' 'make prepare  | prepare a development environment' \
	  'make package  | build the Debian package' 'make install  | install the prepared package' \
	  'make update   | update the installed package' 'make run      | open the configurator'
run:
	@/bin/sh ./libexec/run
prepare:
	uv sync --locked --all-groups
include mk/files.mk mk/projects.mk mk/base.mk mk/admin.mk mk/workspace.mk mk/package.mk mk/quality.mk
