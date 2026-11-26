.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: help create-context setup-mode login logout configure remove-owned-contexts start-service stop-service prune install update uninstall start-system-service stop-system-service
help:
	@echo 'Open the plugin page to review an action.'
create-context setup-mode login logout configure remove-owned-contexts start-service stop-service prune:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_docker.action "$@"
install update uninstall start-system-service stop-system-service:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play docker "$@"
