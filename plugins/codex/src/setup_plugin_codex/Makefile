.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: help install update uninstall configure configure-mcp login logout
help:
	@echo 'Open the plugin page to review an action.'
install update uninstall configure configure-mcp login logout:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_codex.action "$@"
