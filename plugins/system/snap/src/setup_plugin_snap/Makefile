.DEFAULT_GOAL := help
override SHELL := /bin/sh
.PHONY: help install update uninstall start-system-service
help:
	@echo 'Open the Snap plugin to review runtime and inventory actions.'
install update uninstall start-system-service:
	@/usr/bin/python3 -I -B -m setup_system_sdk.native_play snap "$@"
