.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: open-editor open-codex help prepare-directories template-cache template-create template-update
help:
	@echo "Open the plugin page to preview an action."
prepare-directories:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_projects.action prepare-directories
template-cache:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_projects.action template-cache
template-create:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_projects.action template-create
template-update:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_projects.action template-update
open-editor open-codex:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_projects.action $@
