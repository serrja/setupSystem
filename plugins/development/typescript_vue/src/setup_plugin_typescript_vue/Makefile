.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: help configure remove-tools install-plugins install-parsers verify-editor install update uninstall
help:
	@echo 'Open the plugin page to review an action.'
configure remove-tools install-plugins install-parsers verify-editor:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_typescript_vue.action "$@"
install update uninstall:
	@/usr/bin/python3 -I -B -m setup_system_sdk.native_play typescript_vue "$@"
