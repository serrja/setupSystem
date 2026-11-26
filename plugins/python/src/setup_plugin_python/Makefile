.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: help configure remove-tools install update uninstall
help:
	@echo 'Open the plugin page to review an action.'
configure remove-tools:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_python.action "$@"
install update uninstall:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play python "$@"
