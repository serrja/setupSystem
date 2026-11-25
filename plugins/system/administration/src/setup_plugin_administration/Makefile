.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: help grant-sudo verify-sudo
help:
	@echo "Open the plugin page to preview an action."
grant-sudo:
	@/usr/bin/python3 -I -B -m setup_system_sdk.native_play administration grant-sudo
verify-sudo:
	@/usr/bin/python3 -I -B -m setup_system_sdk.native_play administration verify-sudo
