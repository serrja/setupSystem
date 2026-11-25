PACKAGE_DIR ?= dist
BUILD_PYTHON ?= $(if $(wildcard .venv/bin/python),$(CURDIR)/.venv/bin/python,python3)
.PHONY: package
package:
	@"$(BUILD_PYTHON)" -I -B debian/build.py --output "$(PACKAGE_DIR)" $(if $(VERSION),--version "$(VERSION)")

.PHONY: install update
install:
	@/bin/sh debian/install-local.sh "$(PACKAGE_DIR)"
update:
	@/bin/sh ./install.sh update $(if $(VERSION),--release "v$(VERSION)")
