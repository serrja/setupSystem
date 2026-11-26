UV ?= uv
PACKAGE_DIR ?= dist
.PHONY: package install
package:
	$(UV) run --package setup-system-plugin-debian python packaging/build.py --host-wheel "$(HOST_WHEEL)" --sdk-wheel "$(SDK_WHEEL)" $(foreach release,$(RELEASES),--release "$(release)") --version "$(VERSION)" --output "$(PACKAGE_DIR)"
install:
	/bin/sh packaging/install-local.sh "$(PACKAGE_DIR)"
