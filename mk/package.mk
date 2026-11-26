PACKAGE_DIR ?= dist
override export PACKAGE_DIR := $(value PACKAGE_DIR)
override export VERSION := $(value VERSION)
.PHONY: package install update

package:
	@set -eu; python=python3; if [ -x .venv/bin/python ]; then python=.venv/bin/python; fi; \
	  set -- -I -B debian/build.py --output "$$PACKAGE_DIR"; \
	  if [ -n "$$VERSION" ]; then set -- "$$@" --version "$$VERSION"; fi; \
	  exec "$$python" "$$@"

install:
	@/bin/sh debian/install-local.sh "$$PACKAGE_DIR"

update:
	@set -- update; if [ -n "$$VERSION" ]; then set -- "$$@" --release "v$$VERSION"; fi; \
	  exec /bin/sh ./install.sh "$$@"
