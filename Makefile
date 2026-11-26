.DEFAULT_GOAL := help
UV ?= uv
.PHONY: help prepare run migrate build plugins-check check ci
help:
	@printf '%s\n' 'make prepare  Prepare the host development environment' \
	  'make check    Run host and SDK checks' 'make build    Build the host and SDK distributions' \
	  'make plugins-check    Run package-owned checks'
prepare:
	$(UV) sync --locked --package setup-system
run:
	$(UV) run --locked setup-system
migrate:
	$(UV) run --locked setup-system migrate
build:
	$(UV) build --package setup-system-sdk
	$(UV) build --package setup-system
plugins-check:
	@set -e; for file in $(sort $(wildcard plugins/*/ci/Makefile)); do \
	  $(MAKE) -C "$${file%/ci/Makefile}" -f ci/Makefile check; done
include mk/quality.mk
.PHONY: check-installed
check-installed: build
	$(UV) run --locked python docker/installed_environment.py dist
ci: check check-installed plugins-check
