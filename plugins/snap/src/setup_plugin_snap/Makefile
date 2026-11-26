.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: help install update uninstall start-system-service install-apps update-apps remove-apps
help:
	@echo 'Open Snap to inspect snapd or review a catalog application.'
install update uninstall start-system-service install-apps update-apps remove-apps:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play snap "$@"
