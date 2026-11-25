.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: install update help timezone locale keyboard
help:
	@echo "Open the plugin page to preview an action."
timezone:
	@/usr/bin/python3 -I -B -m setup_system_sdk.native_play regional timezone
locale:
	@/usr/bin/python3 -I -B -m setup_system_sdk.native_play regional locale
keyboard:
	@/usr/bin/python3 -I -B -m setup_system_sdk.native_play regional keyboard
install update:
	@/usr/bin/python3 -I -B -m setup_system_sdk.native_play regional $@
