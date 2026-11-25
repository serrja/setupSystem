.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: help save-subscription export-route forget-local forget-owned-files install update uninstall start-system-service stop-system-service
help:
	@echo 'Open the plugin page to review an action.'
save-subscription export-route forget-local forget-owned-files:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_happ.action "$@"
install update uninstall start-system-service stop-system-service:
	@/usr/bin/python3 -I -B -m setup_system_sdk.native_play happ "$@"
