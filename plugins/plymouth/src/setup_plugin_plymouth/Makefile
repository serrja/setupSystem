.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: help install update configure
help:
	@echo "Open the plugin page to preview an action."
install:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play plymouth install
update:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play plymouth update
configure:
	@/usr/bin/python3 -I -B -m setup_system.execution.recipes.native_play plymouth configure
