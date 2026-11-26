.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: uninstall help install update activate-login
help:
	@echo "Open the plugin page to preview an action."
install:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play greeter install
update:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play greeter update
activate-login:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play greeter activate-login
uninstall:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play greeter uninstall
