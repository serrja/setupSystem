# Freeze inherited strings before GNU Make exports its recipe environment.
override SHELL := /bin/sh
override define setup_export_literal
ifneq ($$(origin $(1)),undefined)
override export $(1) := $$(value $(1))
endif
endef
$(foreach item,HOME XDG_CONFIG_HOME XDG_DATA_HOME XDG_STATE_HOME XDG_CACHE_HOME XDG_RUNTIME_DIR HYPRLAND_INSTANCE_SIGNATURE CODEX_HOME SSH_AUTH_SOCK DISPLAY WAYLAND_DISPLAY SETUP_SYSTEM_REQUEST SETUP_SYSTEM_RESULT SETUP_SYSTEM_CANCEL SETUP_SYSTEM_INTERPRETER,$(eval $(call setup_export_literal,$(item))))
