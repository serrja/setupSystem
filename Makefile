.DEFAULT_GOAL := help
UV ?= uv
.PHONY: help prepare run migrate build plugins-check check ci
help:
	@printf '%s\n' 'make prepare  Prepare the host development environment' \
	  'make check    Run host and SDK checks' 'make build    Build the host and SDK distributions' \
	  'make plugin-types    Type-check plugin sources' 'make plugins-check    Run package-owned checks'
prepare:
	$(UV) sync --locked --package setup-system $(if $(PLUGIN),--package setup-system-plugin-$(PLUGIN))
run:
	$(UV) run --locked setup-system
migrate:
	$(UV) run --locked setup-system migrate
build:
	$(UV) build --package setup-system-desktop-theme
	$(UV) build --package setup-system-sdk
	$(UV) build --package setup-system
.PHONY: plugin-types
PLUGIN_SOURCES := $(wildcard plugins/*/src plugins/*/*/src)
plugin-types:
	MYPYPATH=$$(printf '%s\n' $(PLUGIN_SOURCES) | paste -sd:) $(UV) run --locked --all-packages --all-groups mypy $(PLUGIN_SOURCES)
plugins-check:
	@set -e; for file in $(sort $(wildcard plugins/*/ci/Makefile)); do \
	  $(MAKE) -C "$${file%/ci/Makefile}" -f ci/Makefile check; done
include mk/quality.mk
.PHONY: check-installed
check-installed: build
	$(UV) run --locked python docker/installed_environment.py dist
ci: check check-installed plugin-types plugins-check
