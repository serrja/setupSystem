UV ?= uv
PACKAGE_DIR ?= dist
SOURCE_COMMIT ?= $(shell git rev-parse HEAD)
.PHONY: package install
package:
	$(UV) run --locked --package setup-system-plugin-debian python packaging/build.py \
	  --source "$(SOURCE_COMMIT)" --host-wheel "$(HOST_WHEEL)" --sdk-wheel "$(SDK_WHEEL)" \
	  $(foreach release,$(RELEASES),--release "$(release)") --runtime-assets "$(RUNTIME_ASSETS)" \
	  --version "$(VERSION)" --output "$(PACKAGE_DIR)"
install:
	/bin/sh packaging/install-local.sh "$(PACKAGE_DIR)"
