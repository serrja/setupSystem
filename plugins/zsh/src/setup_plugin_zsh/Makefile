.DEFAULT_GOAL := help
.PHONY: help install update uninstall configure
help:
	@echo 'Open the plugin page to preview an action.'
install update uninstall:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play zsh "$@"
configure:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_zsh.action "$@"
