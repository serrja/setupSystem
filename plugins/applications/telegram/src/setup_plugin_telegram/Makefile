.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: configure-launcher help install update uninstall
help:
	@echo 'Open the plugin page to review an action.'
install update uninstall:
	@/usr/bin/python3 -I -B -m setup_system_sdk.native_play telegram "$@"
configure-launcher:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_telegram.action "$@"
