UV ?= uv
.PHONY: check
check:
	$(UV) run --locked --package setup-system-plugin-debian ruff check src tooling packaging
	$(UV) run --locked --package setup-system-plugin-debian ansible-lint src/setup_plugin_debian/resources/*.yml
	$(MAKE) -C ../.. infrastructure-check PLUGIN=debian
	$(UV) build --package setup-system-plugin-debian
	$(UV) run --locked --package setup-system-plugin-debian python tooling/check_package.py
