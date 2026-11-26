.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: uninstall help install update configure
help:
	@echo "Open the plugin page to preview an action."
install:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play foot install
update:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play foot update
configure:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_foot.action configure
uninstall:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play foot uninstall
