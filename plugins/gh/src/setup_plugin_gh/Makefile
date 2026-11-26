.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: help login logout install update uninstall
help:
	@echo 'Open the plugin page to review an action.'
login logout:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_gh.action "$@"
install update uninstall:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play gh "$@"
