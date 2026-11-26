.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: uninstall help install update configure
help:
	@echo "Open the plugin page to preview an action."
install:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play waybar install
update:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play waybar update
configure:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_waybar.action configure
uninstall:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play waybar uninstall
