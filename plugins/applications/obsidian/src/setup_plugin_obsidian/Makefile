.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: help create-vault open set-theme core-feature install update uninstall
help:
	@echo 'Open the plugin page to review an action.'
create-vault open set-theme core-feature:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_obsidian.action "$@"
install update uninstall:
	@/usr/bin/python3 -I -B -m setup_system_sdk.native_play obsidian "$@"
