.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: uninstall help install update configure save-monitor
help:
	@echo "Open the plugin page to preview an action."
install:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play hyprland install
update:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play hyprland update
configure:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_hyprland.action configure
save-monitor:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_hyprland.action save-monitor
uninstall:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play hyprland uninstall
.PHONY: test-monitor
test-monitor:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_hyprland.action test-monitor
