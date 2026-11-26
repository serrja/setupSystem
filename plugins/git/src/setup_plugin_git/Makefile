.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: help configure configure-style import-identity use-identity upload-key revoke-key generate-key remove-local-keys remove-owned-keys install update uninstall remove-retired-package
help:
	@echo 'Open the plugin page to review an action.'
configure configure-style import-identity use-identity upload-key revoke-key generate-key remove-local-keys remove-owned-keys:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_git.action "$@"
install update uninstall remove-retired-package:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play git "$@"
