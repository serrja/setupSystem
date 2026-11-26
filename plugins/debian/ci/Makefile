UV ?= uv
.PHONY: check
check:
	$(UV) run --locked --package setup-system-plugin-debian ruff check src tests tooling packaging
	$(UV) run --locked --package setup-system-plugin-debian ansible-lint src/setup_plugin_debian/resources/*.yml
	$(UV) run --locked --package setup-system-plugin-debian pytest tests
	$(UV) build --package setup-system-plugin-debian
	$(UV) run --locked --package setup-system-plugin-debian python tooling/check_package.py
