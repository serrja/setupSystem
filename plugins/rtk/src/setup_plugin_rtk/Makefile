.DEFAULT_GOAL := help
.PHONY: help install update uninstall
help:
	@echo 'Open the plugin page to preview an action.'
install update uninstall:
	@"$${SETUP_SYSTEM_INTERPRETER}" -I -B -m setup_plugin_rtk.action "$@"
