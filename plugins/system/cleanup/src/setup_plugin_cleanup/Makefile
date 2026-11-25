.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: help remove purge autoremove
help:
	@echo "Open the plugin page to preview an action."
remove:
	@/usr/bin/python3 -I -B -m setup_system_sdk.native_play cleanup remove
purge:
	@/usr/bin/python3 -I -B -m setup_system_sdk.native_play cleanup purge
autoremove:
	@/usr/bin/python3 -I -B -m setup_system_sdk.native_play cleanup autoremove
