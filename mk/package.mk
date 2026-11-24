PACKAGE_DIR ?= dist
.PHONY: package
package:
	@/usr/bin/python3 debian/build.py --output "$(PACKAGE_DIR)" $(if $(VERSION),--version "$(VERSION)")

.PHONY: install update
install:
	@/bin/sh debian/install-local "$(PACKAGE_DIR)"
update:
	@/bin/sh ./install.sh update $(if $(VERSION),--release "v$(VERSION)")
