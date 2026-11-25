.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: help install update uninstall configure remove-policy
help:
	@echo 'Open the plugin page to review an action.'
install update uninstall configure remove-policy:
	@/usr/bin/python3 -I -B -m setup_system_sdk.native_play firefox "$@"
