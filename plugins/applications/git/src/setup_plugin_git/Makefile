.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: help configure configure-style import-profile use-profile upload-key revoke-key logout remove-owned-keys install update uninstall
help:
	@echo 'Open the plugin page to review an action.'
configure configure-style import-profile use-profile upload-key revoke-key logout remove-owned-keys:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_git.action "$@"
install update uninstall:
	@/usr/bin/python3 -I -B -m setup_system_sdk.native_play git "$@"
