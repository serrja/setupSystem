.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: uninstall help install update configure
help:
	@echo "Open the plugin page to preview an action."
install:
	@/usr/bin/python3 -I -B -m setup_system_sdk.native_play hyprlock install
update:
	@/usr/bin/python3 -I -B -m setup_system_sdk.native_play hyprlock update
configure:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_hyprlock.action configure
uninstall:
	@/usr/bin/python3 -I -B -m setup_system_sdk.native_play hyprlock uninstall
